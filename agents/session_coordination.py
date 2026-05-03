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
# MCP OAuth 2.0 — readiness counter + pub/sub (WebSocket push)
# ---------------------------------------------------------------------------

_MCP_OAUTH_READINESS_TTL: Final[int] = 86400  # 24 hours (matches attachment cache)


def _mcp_oauth_server_count_key(session_id: str) -> str:
    """Redis key holding the count of already-authorized OAuth servers for a session."""
    return f"{_namespace()}:mcp_oauth:run:{session_id}:servers"


def _mcp_oauth_pubsub_channel(session_id: str) -> str:
    """Redis pub/sub channel name for OAuth readiness events for a session."""
    return f"{_namespace()}:mcp_oauth:readiness:{session_id}"


def init_mcp_oauth_readiness(session_id: str, authorized_count: int) -> None:
    """Initialise (or reset) the authorisation counter for an OAuth gate.

    Sets the counter to ``authorized_count`` with a 24-hour TTL.  Call this
    once just before returning the 409 response or the SSE ``awaiting_mcp_oauth``
    event so the WebSocket consumer can compute the initial per-server state
    without an extra DB round-trip.
    """
    key = _mcp_oauth_server_count_key(session_id)
    try:
        _get_client().set(key, authorized_count, ex=_MCP_OAUTH_READINESS_TTL)
    except Exception:  # noqa: BLE001
        logger.warning(
            "agents.mcp.oauth_readiness_init_failed",
            extra={"session_id": session_id},
        )


def get_mcp_oauth_authorized_count(session_id: str) -> int:
    """Return the number of OAuth servers already authorized for this session (0 if unknown)."""
    key = _mcp_oauth_server_count_key(session_id)
    try:
        raw = _get_client().get(key)
        return int(raw) if raw is not None else 0
    except Exception:  # noqa: BLE001
        return 0


def publish_oauth_server_authorized(
    session_id: str,
    server_name: str,
    total_count: int,
) -> int:
    """Atomically increment the authorized-server counter and publish a readiness event.

    Returns the new authorized count after incrementing.  Publishes to
    ``_mcp_oauth_pubsub_channel(session_id)`` so the WebSocket consumer can push
    the update to the browser without polling.

    The publish JSON payload is::

        {"server_name": <str>, "authorized_count": <int>, "total_count": <int>}
    """
    import json as _json

    key = _mcp_oauth_server_count_key(session_id)
    channel = _mcp_oauth_pubsub_channel(session_id)
    try:
        client = _get_client()
        new_count = client.incr(key)
        # Refresh TTL after increment so it doesn't silently expire mid-session.
        client.expire(key, _MCP_OAUTH_READINESS_TTL)
        payload = _json.dumps({
            "server_name": server_name,
            "authorized_count": new_count,
            "total_count": total_count,
        })
        client.publish(channel, payload)
        logger.info(
            "agents.mcp.oauth_readiness_published",
            extra={
                "session_id": session_id,
                "server_name": server_name,
                "authorized_count": new_count,
                "total_count": total_count,
            },
        )
        return new_count
    except Exception:  # noqa: BLE001
        logger.warning(
            "agents.mcp.oauth_readiness_publish_failed",
            extra={"session_id": session_id, "server_name": server_name},
        )
        return 0


def delete_mcp_oauth_readiness(session_id: str) -> None:
    """Delete the readiness counter key when a run successfully starts.

    Silent on failure — a stale key will expire naturally after 24 hours.
    """
    key = _mcp_oauth_server_count_key(session_id)
    try:
        _get_client().delete(key)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "SessionCoordinationError",
    "acquire_run_lease",
    "clear_cancel_signal",
    "clear_run_traceparent",
    "delete_mcp_oauth_readiness",
    "ensure_redis_available",
    "get_heartbeat_interval_seconds",
    "get_instance_id",
    "get_mcp_oauth_authorized_count",
    "get_mcp_oauth_test_status",
    "get_mcp_oauth_token",
    "get_and_delete_mcp_oauth_state",
    "get_redis_client",
    "get_run_traceparent",
    "init_mcp_oauth_readiness",
    "is_cancel_signaled",
    "list_authorized_oauth_servers",
    "publish_oauth_server_authorized",
    "purge_mcp_oauth_tokens",
    "release_run_lease",
    "renew_run_lease",
    "set_mcp_oauth_state",
    "set_mcp_oauth_test_status",
    "set_mcp_oauth_token",
    "signal_cancel",
    "store_run_traceparent",
]
