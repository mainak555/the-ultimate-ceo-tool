"""Redis-backed coordination for active chat session runs.

Redis stores ephemeral coordination state only:
- one active run lease per session_id
- cross-instance cancel signal per session_id

Durable conversation and AutoGen resume state remain in MongoDB.
"""

from __future__ import annotations

import logging
import os
import socket
from importlib import import_module
from typing import Final

from django.conf import settings

from core.tracing import traced_function

logger = logging.getLogger(__name__)

_LEASE_LUA: Final[str] = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""

_RELEASE_LUA: Final[str] = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

class SessionCoordinationError(RuntimeError):
    """Raised when Redis coordination operations cannot be completed."""


_REDIS_CLIENT = None
_INSTANCE_ID: Final[str] = f"{socket.gethostname()}:{os.getpid()}"


def get_instance_id() -> str:
    """Return a stable worker identifier for lease ownership."""
    return _INSTANCE_ID


def get_heartbeat_interval_seconds() -> int:
    """Return lease heartbeat interval in seconds (minimum 5)."""
    raw = int(getattr(settings, "REDIS_RUN_HEARTBEAT_SECONDS", 20) or 20)
    return max(5, raw)


def _lease_ttl_seconds() -> int:
    raw = int(getattr(settings, "REDIS_RUN_LEASE_TTL_SECONDS", 300) or 300)
    return max(30, raw)


def _cancel_ttl_seconds() -> int:
    raw = int(getattr(settings, "REDIS_CANCEL_SIGNAL_TTL_SECONDS", 120) or 120)
    return max(10, raw)


def _namespace() -> str:
    ns = (getattr(settings, "REDIS_NAMESPACE", "product_discovery") or "product_discovery").strip()
    return ns or "product_discovery"


def _session_key(session_id: str, suffix: str) -> str:
    return f"{_namespace()}:chat_session:{session_id}:{suffix}"


def _lease_key(session_id: str) -> str:
    return _session_key(session_id, "active_lease")


def _cancel_key(session_id: str) -> str:
    return _session_key(session_id, "cancel")


def _run_trace_key(session_id: str) -> str:
    return _session_key(session_id, "run_trace")


def _get_client():
    """Return a cached Redis client configured from Django settings."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT

    try:
        redis_mod = import_module("redis")
        redis_cls = getattr(redis_mod, "Redis")
        _REDIS_CLIENT = redis_cls.from_url(
            getattr(settings, "REDIS_URI", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_timeout=float(getattr(settings, "REDIS_SOCKET_TIMEOUT", 2.0) or 2.0),
            socket_connect_timeout=float(getattr(settings, "REDIS_SOCKET_CONNECT_TIMEOUT", 2.0) or 2.0),
        )
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to initialize Redis client.") from exc

    return _REDIS_CLIENT


def get_redis_client():
    """Return the shared Redis client.

    Exposed for use by other server-layer modules (e.g. ``attachment_service``)
    so they reuse the same connection pool rather than creating a second one.
    Raises :class:`SessionCoordinationError` when Redis is unreachable.
    """
    return _get_client()


@traced_function("agents.session.redis_available")
def ensure_redis_available() -> bool:
    """Return True when Redis responds to ping; False on any connectivity error."""
    try:
        _get_client().ping()
        return True
    except Exception:  # noqa: BLE001
        logger.exception("agents.session.redis_unavailable", extra={"phase": "ping"})
        return False


@traced_function("agents.session.acquire_lease")
def acquire_run_lease(session_id: str, owner_id: str) -> bool:
    """Acquire active-run lease for session_id. Returns False if already leased."""
    key = _lease_key(session_id)
    try:
        acquired = bool(_get_client().set(key, owner_id, ex=_lease_ttl_seconds(), nx=True))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to acquire session lease.") from exc
    if acquired:
        logger.info(
            "agents.session.lease_acquired",
            extra={"session_id": session_id, "owner_id": owner_id},
        )
    else:
        logger.info(
            "agents.session.lease_conflict",
            extra={"session_id": session_id, "owner_id": owner_id},
        )
    return acquired


@traced_function("agents.session.renew_lease")
def renew_run_lease(session_id: str, owner_id: str) -> bool:
    """Renew session lease if owner matches. Returns False when ownership is lost."""
    key = _lease_key(session_id)
    try:
        renewed = bool(_get_client().eval(_LEASE_LUA, 1, key, owner_id, _lease_ttl_seconds()))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to renew session lease.") from exc
    if not renewed:
        logger.warning(
            "agents.session.lease_lost",
            extra={"session_id": session_id, "owner_id": owner_id},
        )
    return renewed


@traced_function("agents.session.release_lease")
def release_run_lease(session_id: str, owner_id: str) -> None:
    """Release session lease when owned by this worker."""
    key = _lease_key(session_id)
    try:
        released = bool(_get_client().eval(_RELEASE_LUA, 1, key, owner_id))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to release session lease.") from exc
    logger.info(
        "agents.session.lease_released",
        extra={
            "session_id": session_id,
            "owner_id": owner_id,
            "released": released,
        },
    )


@traced_function("agents.session.signal_cancel")
def signal_cancel(session_id: str) -> None:
    """Set cross-instance cancel signal for a running session."""
    try:
        _get_client().set(_cancel_key(session_id), "1", ex=_cancel_ttl_seconds())
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to signal cancel.") from exc
    logger.info("agents.session.cancel_signaled", extra={"session_id": session_id})


@traced_function("agents.session.clear_cancel")
def clear_cancel_signal(session_id: str) -> None:
    """Clear stale cancel signal before a new run begins."""
    try:
        _get_client().delete(_cancel_key(session_id))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to clear cancel signal.") from exc


@traced_function("agents.session.cancel_check")
def is_cancel_signaled(session_id: str) -> bool:
    """Return True when a cancel signal exists for this session."""
    try:
        return bool(_get_client().exists(_cancel_key(session_id)))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to check cancel signal.") from exc


# ---------------------------------------------------------------------------
# Per-run trace context (Redis-backed OTel traceparent storage)
# ---------------------------------------------------------------------------

def store_run_traceparent(session_id: str, traceparent: str) -> None:
    """Store the W3C traceparent for the current run's root span.

    TTL matches the run lease so the key expires automatically if the process
    dies without calling ``clear_run_traceparent``. Silent on Redis failure —
    never blocks a run.
    """
    if not traceparent:
        return
    try:
        _get_client().set(_run_trace_key(session_id), traceparent, ex=_lease_ttl_seconds())
    except Exception:  # noqa: BLE001
        pass


def get_run_traceparent(session_id: str) -> str | None:
    """Return the stored W3C traceparent for the current run, or None.

    Silent on Redis failure — callers fall back to no OTel context.
    """
    try:
        return _get_client().get(_run_trace_key(session_id)) or None
    except Exception:  # noqa: BLE001
        return None


def clear_run_traceparent(session_id: str) -> None:
    """Delete the run traceparent key on normal run end.

    Silent on Redis failure — key expires automatically via TTL.
    """
    try:
        _get_client().delete(_run_trace_key(session_id))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# MCP OAuth 2.0 — session-scoped token storage (run-time)
# ---------------------------------------------------------------------------

# Fallback TTL used when the provider's JWT access_token has no parseable `exp`
# claim. The Redis key naturally expires when the token does, so no external
# cap is applied — TTL is authoritative from the JWT itself.
_MCP_OAUTH_DEFAULT_TTL: Final[int] = 3 * 3600  # 3 hours


def _mcp_oauth_token_key(session_id: str, server_name: str) -> str:
    return f"{_namespace()}:mcp_oauth:run:{session_id}:{server_name}:token"


def _mcp_oauth_state_key(state: str) -> str:
    return f"{_namespace()}:mcp_oauth_state:{state}:meta"


def _mcp_oauth_test_key(project_id: str, server_name: str) -> str:
    return f"{_namespace()}:mcp_oauth:test:{project_id}:{server_name}:status"


def set_mcp_oauth_token(
    session_id: str,
    server_name: str,
    access_token: str,
    ttl_seconds: int = _MCP_OAUTH_DEFAULT_TTL,
) -> None:
    """Store a run-time OAuth Bearer token for a specific MCP server + session.

    TTL is derived from the JWT ``exp`` claim (``exp - now()``). The Redis key
    expires exactly when the token does, so cache hits during an active run are
    always valid. Falls back to ``_MCP_OAUTH_DEFAULT_TTL`` (3 h) when ``exp``
    is unavailable. Silent on Redis failure — the run will simply require
    re-authorization on the next run.
    """
    import json as _json

    key = _mcp_oauth_token_key(session_id, server_name)
    # Enforce a floor of 60 s; no ceiling — the JWT exp is authoritative.
    capped_ttl = max(60, int(ttl_seconds))
    try:
        _get_client().set(
            key,
            _json.dumps({"access_token": access_token}),
            ex=capped_ttl,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "agents.mcp.oauth_token_store_failed",
            extra={"session_id": session_id, "server_name": server_name},
        )


def get_mcp_oauth_token(session_id: str, server_name: str) -> str | None:
    """Return the stored OAuth access token for a session+server, or None if missing/expired."""
    import json as _json

    key = _mcp_oauth_token_key(session_id, server_name)
    try:
        raw = _get_client().get(key)
        if not raw:
            return None
        return _json.loads(raw).get("access_token")
    except Exception:  # noqa: BLE001
        return None


def list_authorized_oauth_servers(
    session_id: str, server_names: list[str]
) -> list[str]:
    """Return the subset of ``server_names`` that currently hold a session-scoped
    OAuth token in Redis.

    Uses a pipelined ``EXISTS`` so the cost is one round-trip regardless of the
    number of servers. On any Redis error returns an empty list (caller treats
    every server as unauthorized — fail-closed for the gate).
    """
    if not session_id or not server_names:
        return []
    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        for name in server_names:
            pipe.exists(_mcp_oauth_token_key(session_id, name))
        results = pipe.execute()
        return [
            name
            for name, exists in zip(server_names, results)
            if bool(exists)
        ]
    except Exception:  # noqa: BLE001
        logger.warning(
            "agents.mcp.oauth_list_authorized_failed",
            extra={"session_id": session_id, "server_count": len(server_names)},
        )
        return []


def purge_mcp_oauth_tokens(session_id: str) -> None:
    """Delete all MCP OAuth tokens for a session (called on session delete).

    Uses SCAN to avoid a KEYS call on large Redis instances.
    Silent on Redis failure.
    """
    pattern = f"{_namespace()}:mcp_oauth:run:{session_id}:*:token"
    try:
        client = _get_client()
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                client.delete(*keys)
            if cursor == 0:
                break
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# MCP OAuth 2.0 — PKCE state (short-lived, delete-on-read)
# ---------------------------------------------------------------------------

_MCP_OAUTH_STATE_TTL: Final[int] = 300  # 5 minutes


def set_mcp_oauth_state(state: str, metadata: dict) -> None:
    """Store PKCE + context metadata for an OAuth flow. TTL = 5 minutes.

    metadata keys: session_id | project_id (one of), server_name, mode, code_verifier
    """
    import json as _json

    key = _mcp_oauth_state_key(state)
    try:
        _get_client().set(key, _json.dumps(metadata), ex=_MCP_OAUTH_STATE_TTL)
    except Exception:  # noqa: BLE001
        logger.warning(
            "agents.mcp.oauth_state_store_failed",
            extra={"server_name": metadata.get("server_name", "")},
        )


def get_and_delete_mcp_oauth_state(state: str) -> dict | None:
    """Return stored OAuth state metadata and atomically delete the key (one-time use)."""
    import json as _json

    key = _mcp_oauth_state_key(state)
    try:
        raw = _get_client().getdel(key)
        if not raw:
            return None
        return _json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# MCP OAuth 2.0 — test-mode status (config-form Test Authorization)
# ---------------------------------------------------------------------------

_MCP_OAUTH_TEST_TTL: Final[int] = 600  # 10 minutes


def set_mcp_oauth_test_status(project_id: str, server_name: str) -> None:
    """Mark that a test-mode OAuth flow succeeded for this project+server (10-minute TTL)."""
    key = _mcp_oauth_test_key(project_id, server_name)
    try:
        _get_client().set(key, "ok", ex=_MCP_OAUTH_TEST_TTL)
    except Exception:  # noqa: BLE001
        pass


def get_mcp_oauth_test_status(project_id: str, server_name: str) -> bool:
    """Return True if a recent test-mode OAuth flow succeeded for this project+server."""
    key = _mcp_oauth_test_key(project_id, server_name)
    try:
        return bool(_get_client().exists(key))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Multi-user Human Gate — remote-user readiness state (Phase 2)
# ---------------------------------------------------------------------------
#
# Redis is the only store for these keys. They are deliberately ephemeral
# and reset on session delete. NEVER log token strings or join URLs from
# this section — fingerprints / counts only.
# ---------------------------------------------------------------------------

import secrets as _secrets


def _remote_user_token_ttl() -> int:
    raw = int(getattr(settings, "REMOTE_USER_TOKEN_TTL_SECONDS", 43200) or 43200)
    return max(60, raw)


def _remote_user_presence_ttl() -> int:
    raw = int(getattr(settings, "REMOTE_USER_PRESENCE_TTL_SECONDS", 60) or 60)
    return max(10, raw)


def _remote_user_checked_ttl() -> int:
    # Should outlive any reasonable readiness wait.
    raw = int(getattr(settings, "REMOTE_USER_CHECKED_TTL_SECONDS", 43200) or 43200)
    return max(60, raw)


def get_remote_user_heartbeat_interval_seconds() -> int:
    raw = int(getattr(settings, "REMOTE_USER_HEARTBEAT_INTERVAL_SECONDS", 30) or 30)
    return max(5, raw)


def _remote_user_session_key(session_id: str, bucket: str, *parts: str | int) -> str:
    base = f"{_namespace()}:remote_user:{session_id}:{bucket}"
    if not parts:
        return base
    suffix = ":".join(str(p) for p in parts if str(p).strip())
    if not suffix:
        return base
    return f"{base}:{suffix}"


def _remote_user_token_key(session_id: str, token: str) -> str:
    return _remote_user_session_key(session_id, "token", token)


def _remote_user_token_by_user_key(session_id: str, user_id: str) -> str:
    return _remote_user_session_key(session_id, "token_by_user", user_id)


def _remote_user_online_key(session_id: str, user_id: str) -> str:
    return _remote_user_session_key(session_id, "online", user_id)


def _leader_online_key(session_id: str) -> str:
    return _remote_user_online_key(session_id, "host")


def _remote_user_checked_key(session_id: str) -> str:
    return _remote_user_session_key(session_id, "checked")


def _remote_export_capability_key(session_id: str, capability: str) -> str:
    return _remote_user_session_key(session_id, "export_capability", capability)


def _remote_export_capability_by_user_key(session_id: str, user_id: str) -> str:
    return _remote_user_session_key(session_id, "export_capability_by_user", user_id)


def _remote_gate_response_key(session_id: str, round_no: int, user_id: str) -> str:
    return _remote_user_session_key(session_id, "gate_response", round_no, user_id)


def _remote_gate_response_index_key(session_id: str, round_no: int) -> str:
    return _remote_user_session_key(session_id, "gate_response_index", round_no)


def _remote_gate_required_key(session_id: str, round_no: int) -> str:
    return _remote_user_session_key(session_id, "gate_required", round_no)


@traced_function("agents.remote_user.token_mint")
def mint_remote_user_token(session_id: str, user_id: str) -> str:
    """Mint a fresh single-use-per-rotation token for (session_id, user_id).

    Any previously active token for the same user is invalidated atomically.
    Returns the new opaque token. Tokens are URL-safe random strings; never
    derived from user_id and never logged.
    """
    if not session_id or not user_id:
        raise SessionCoordinationError("session_id and user_id are required.")
    token = _secrets.token_urlsafe(32)
    ttl = _remote_user_token_ttl()
    by_user_key = _remote_user_token_by_user_key(session_id, user_id)
    new_key = _remote_user_token_key(session_id, token)
    try:
        client = _get_client()
        # Invalidate any prior token for this user
        prior = client.get(by_user_key)
        pipe = client.pipeline(transaction=True)
        if prior:
            pipe.delete(_remote_user_token_key(session_id, prior))
        pipe.set(new_key, user_id, ex=ttl)
        pipe.set(by_user_key, token, ex=ttl)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to mint remote-user token.") from exc
    logger.info(
        "agents.remote_user.token_minted",
        extra={"session_id": session_id, "user_id": user_id, "rotated": bool(prior)},
    )
    return token


def lookup_remote_user_token(session_id: str, token: str) -> str | None:
    """Resolve a token to its user_id, or None when missing/expired."""
    if not session_id or not token:
        return None
    try:
        return _get_client().get(_remote_user_token_key(session_id, token))
    except Exception:  # noqa: BLE001
        return None


def get_remote_user_token_for_user(session_id: str, user_id: str) -> str | None:
    """Return the active token for ``(session_id, user_id)`` or ``None``.

    Used to render a stable invitation link in the readiness lobby without
    rotating the token. Token value is never logged.
    """
    if not session_id or not user_id:
        return None
    try:
        return _get_client().get(_remote_user_token_by_user_key(session_id, user_id))
    except Exception:  # noqa: BLE001
        return None


def get_or_mint_remote_user_token(session_id: str, user_id: str) -> str:
    """Return the existing token for ``(session_id, user_id)`` or mint a fresh one.

    Reuse is the default — an invitation link stays stable for the configured
    Redis TTL so the leader can copy it once and the URL keeps working. Use
    :func:`mint_remote_user_token` directly only when an explicit rotation is
    required.
    """
    existing = get_remote_user_token_for_user(session_id, user_id)
    if existing:
        return existing
    return mint_remote_user_token(session_id, user_id)


@traced_function("agents.remote_user.export_capability_mint")
def mint_remote_export_capability(session_id: str, user_id: str) -> str:
    """Mint a session-scoped remote export capability token for one user.

    Any previously active capability for the same user is invalidated
    atomically. Token values are opaque random strings and are never logged.
    """
    if not session_id or not user_id:
        raise SessionCoordinationError("session_id and user_id are required.")
    capability = _secrets.token_urlsafe(32)
    ttl = _remote_user_token_ttl()
    by_user_key = _remote_export_capability_by_user_key(session_id, user_id)
    cap_key = _remote_export_capability_key(session_id, capability)
    try:
        client = _get_client()
        prior = client.get(by_user_key)
        pipe = client.pipeline(transaction=True)
        if prior:
            pipe.delete(_remote_export_capability_key(session_id, prior))
        pipe.set(cap_key, user_id, ex=ttl)
        pipe.set(by_user_key, capability, ex=ttl)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to mint remote export capability.") from exc
    logger.info(
        "agents.remote_user.export_capability_minted",
        extra={"session_id": session_id, "user_id": user_id, "rotated": bool(prior)},
    )
    return capability


def get_or_mint_remote_export_capability(session_id: str, user_id: str) -> str:
    """Return existing remote export capability for user or mint one."""
    if not session_id or not user_id:
        raise SessionCoordinationError("session_id and user_id are required.")
    try:
        existing = _get_client().get(_remote_export_capability_by_user_key(session_id, user_id))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to read remote export capability.") from exc
    if existing:
        return existing
    return mint_remote_export_capability(session_id, user_id)


def lookup_remote_export_capability(session_id: str, capability: str) -> str | None:
    """Resolve a remote export capability to user_id, or None when missing."""
    if not session_id or not capability:
        return None
    try:
        return _get_client().get(_remote_export_capability_key(session_id, capability))
    except Exception:  # noqa: BLE001
        return None


def set_remote_user_online(session_id: str, user_id: str) -> None:
    """Mark a remote user as online (refreshes presence TTL)."""
    if not session_id or not user_id:
        return
    ttl = _remote_user_presence_ttl()
    try:
        _get_client().set(_remote_user_online_key(session_id, user_id), "1", ex=ttl)
    except Exception:  # noqa: BLE001
        pass


def clear_remote_user_online(session_id: str, user_id: str) -> None:
    """Immediately mark a remote user as offline (e.g. on WS disconnect)."""
    if not session_id or not user_id:
        return
    try:
        _get_client().delete(_remote_user_online_key(session_id, user_id))
    except Exception:  # noqa: BLE001
        pass


def set_leader_online(session_id: str) -> None:
    """Mark the leader UI as online for a session (refreshes presence TTL)."""
    if not session_id:
        return
    ttl = _remote_user_presence_ttl()
    try:
        _get_client().set(_leader_online_key(session_id), "1", ex=ttl)
    except Exception:  # noqa: BLE001
        pass


def clear_leader_online(session_id: str) -> None:
    """Immediately mark the leader UI as offline (e.g. on WS disconnect)."""
    if not session_id:
        return
    try:
        _get_client().delete(_leader_online_key(session_id))
    except Exception:  # noqa: BLE001
        pass


def is_leader_online(session_id: str) -> bool:
    """Return whether leader presence is currently online for a session."""
    if not session_id:
        return False
    try:
        return bool(_get_client().exists(_leader_online_key(session_id)))
    except Exception:  # noqa: BLE001
        # Keep previous UX on transient Redis failures.
        return True


def list_online_remote_users(session_id: str, user_ids: list[str]) -> list[str]:
    """Return the subset of user_ids currently marked online for this session."""
    if not session_id or not user_ids:
        return []
    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        for uid in user_ids:
            pipe.exists(_remote_user_online_key(session_id, uid))
        results = pipe.execute()
        return [uid for uid, ex in zip(user_ids, results) if bool(ex)]
    except Exception:  # noqa: BLE001
        return []


def set_checked_remote_users(session_id: str, user_ids: list[str]) -> None:
    """Persist the leader's selected (checked) remote-user set for this session.

    An empty list explicitly means 'no remote users required for this run' —
    distinct from the default (key absent) which means 'wait for everyone'.
    """
    import json as _json

    if not session_id:
        return
    cleaned = [str(u) for u in (user_ids or []) if isinstance(u, str)]
    try:
        _get_client().set(
            _remote_user_checked_key(session_id),
            _json.dumps(cleaned),
            ex=_remote_user_checked_ttl(),
        )
    except Exception:  # noqa: BLE001
        pass


def get_checked_remote_users(session_id: str) -> list[str] | None:
    """Return the persisted checked-set, or None when the leader has not chosen yet."""
    import json as _json

    if not session_id:
        return None
    try:
        raw = _get_client().get(_remote_user_checked_key(session_id))
        if raw is None:
            return None
        decoded = _json.loads(raw)
        if not isinstance(decoded, list):
            return None
        return [str(u) for u in decoded if isinstance(u, str)]
    except Exception:  # noqa: BLE001
        return None


def list_remote_users_with_token(session_id: str, user_ids: list[str]) -> list[str]:
    """Return the subset of user_ids that currently hold an active token.

    Uses a pipelined ``EXISTS`` so cost is one round-trip. Never reads the
    token value itself.
    """
    if not session_id or not user_ids:
        return []
    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        for uid in user_ids:
            pipe.exists(_remote_user_token_by_user_key(session_id, uid))
        results = pipe.execute()
        return [uid for uid, ex in zip(user_ids, results) if bool(ex)]
    except Exception:  # noqa: BLE001
        return []


def initialize_remote_gate_round(session_id: str, round_no: int, required_user_ids: list[str]) -> None:
    """Initialize required-user and response index state for a gate round."""
    import json as _json

    if not session_id or round_no <= 0:
        return
    required = [str(uid).strip() for uid in (required_user_ids or []) if str(uid).strip()]
    ttl = _remote_user_checked_ttl()
    required_key = _remote_gate_required_key(session_id, round_no)
    response_idx_key = _remote_gate_response_index_key(session_id, round_no)
    try:
        client = _get_client()
        client.set(required_key, _json.dumps(required), ex=ttl)
        client.delete(response_idx_key)
        client.expire(response_idx_key, ttl)
    except Exception:  # noqa: BLE001
        pass


def get_remote_gate_required_users(session_id: str, round_no: int) -> list[str] | None:
    """Return required remote users for a gate round, or None if unset."""
    import json as _json

    if not session_id or round_no <= 0:
        return None
    try:
        raw = _get_client().get(_remote_gate_required_key(session_id, round_no))
        if raw is None:
            return None
        decoded = _json.loads(raw)
        if not isinstance(decoded, list):
            return None
        return [str(uid) for uid in decoded if isinstance(uid, str)]
    except Exception:  # noqa: BLE001
        return None


@traced_function("agents.remote_user.gate_response_record")
def record_remote_gate_response(
    session_id: str,
    round_no: int,
    user_id: str,
    payload_json: str,
) -> None:
    """Record one remote response per user for a gate round.

    The per-user key prevents duplicate responses from the same user in one
    round. The index set supports efficient collection and cleanup.
    """
    if not session_id or round_no <= 0 or not user_id:
        return
    ttl = _remote_user_checked_ttl()
    response_key = _remote_gate_response_key(session_id, round_no, user_id)
    response_idx_key = _remote_gate_response_index_key(session_id, round_no)
    try:
        client = _get_client()
        if client.set(response_key, payload_json, ex=ttl, nx=True):
            pipe = client.pipeline(transaction=False)
            pipe.sadd(response_idx_key, user_id)
            pipe.expire(response_idx_key, ttl)
            pipe.execute()
            logger.info(
                "agents.remote_user.gate_response_recorded",
                extra={"session_id": session_id, "user_id": user_id, "round": round_no},
            )
    except Exception:  # noqa: BLE001
        pass


def list_remote_gate_responded_users(session_id: str, round_no: int) -> list[str]:
    """Return user IDs that have already responded for a gate round."""
    if not session_id or round_no <= 0:
        return []
    try:
        raw = _get_client().smembers(_remote_gate_response_index_key(session_id, round_no))
        return [str(uid) for uid in (raw or []) if isinstance(uid, str)]
    except Exception:  # noqa: BLE001
        return []


def pop_remote_gate_response_payloads(session_id: str, round_no: int) -> list[str]:
    """Return and clear all remote response payload JSON strings for a round."""
    if not session_id or round_no <= 0:
        return []
    response_idx_key = _remote_gate_response_index_key(session_id, round_no)
    try:
        client = _get_client()
        user_ids = client.smembers(response_idx_key) or []
        if not user_ids:
            return []
        pipe = client.pipeline(transaction=False)
        for uid in user_ids:
            pipe.get(_remote_gate_response_key(session_id, round_no, uid))
        payloads = pipe.execute() or []
        cleanup_keys = [_remote_gate_response_key(session_id, round_no, uid) for uid in user_ids]
        cleanup_keys.append(response_idx_key)
        cleanup_keys.append(_remote_gate_required_key(session_id, round_no))
        client.delete(*cleanup_keys)
        return [str(p) for p in payloads if isinstance(p, str) and p.strip()]
    except Exception:  # noqa: BLE001
        return []


def purge_remote_users_state(session_id: str) -> None:
    """Delete all readiness/presence/token keys for a session (called on session delete)."""
    if not session_id:
        return
    patterns = [
        _remote_user_session_key(session_id, "token", "*"),
        _remote_user_session_key(session_id, "token_by_user", "*"),
        _remote_user_session_key(session_id, "online", "*"),
        _remote_user_session_key(session_id, "checked"),
        _remote_user_session_key(session_id, "export_capability", "*"),
        _remote_user_session_key(session_id, "export_capability_by_user", "*"),
        _remote_user_session_key(session_id, "gate_response", "*"),
        _remote_user_session_key(session_id, "gate_response_index", "*"),
        _remote_user_session_key(session_id, "gate_required", "*"),
        # Legacy key shapes (pre-unification) for cleanup during migration.
        _remote_user_session_key(session_id, "leader_online"),
        f"{_namespace()}:remote_user_token:{session_id}:*",
        f"{_namespace()}:remote_user_token_by_user:{session_id}:*",
        f"{_namespace()}:remote_user_online:{session_id}:*",
        f"{_namespace()}:leader_online:{session_id}",
        f"{_namespace()}:remote_user_checked:{session_id}",
        f"{_namespace()}:remote_export_capability:{session_id}:*",
        f"{_namespace()}:remote_export_capability_by_user:{session_id}:*",
        f"{_namespace()}:remote_gate_response:{session_id}:*",
        f"{_namespace()}:remote_gate_response_index:{session_id}:*",
        f"{_namespace()}:remote_gate_required:{session_id}:*",
    ]
    try:
        client = _get_client()
        for pattern in patterns:
            if "*" not in pattern:
                client.delete(pattern)
                continue
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    client.delete(*keys)
                if cursor == 0:
                    break
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "SessionCoordinationError",
    "acquire_run_lease",
    "clear_cancel_signal",
    "clear_remote_user_online",
    "clear_run_traceparent",
    "ensure_redis_available",
    "get_checked_remote_users",
    "get_heartbeat_interval_seconds",
    "get_instance_id",
    "is_leader_online",
    "get_mcp_oauth_test_status",
    "get_mcp_oauth_token",
    "get_and_delete_mcp_oauth_state",
    "get_redis_client",
    "get_remote_user_heartbeat_interval_seconds",
    "get_remote_user_token_for_user",
    "get_or_mint_remote_user_token",
    "get_or_mint_remote_export_capability",
    "get_run_traceparent",
    "get_remote_gate_required_users",
    "is_cancel_signaled",
    "initialize_remote_gate_round",
    "list_authorized_oauth_servers",
    "list_remote_gate_responded_users",
    "list_online_remote_users",
    "list_remote_users_with_token",
    "lookup_remote_export_capability",
    "lookup_remote_user_token",
    "mint_remote_export_capability",
    "mint_remote_user_token",
    "pop_remote_gate_response_payloads",
    "purge_mcp_oauth_tokens",
    "purge_remote_users_state",
    "record_remote_gate_response",
    "release_run_lease",
    "renew_run_lease",
    "set_checked_remote_users",
    "set_leader_online",
    "set_mcp_oauth_state",
    "set_mcp_oauth_test_status",
    "set_mcp_oauth_token",
    "set_remote_user_online",
    "signal_cancel",
    "store_run_traceparent",
]
