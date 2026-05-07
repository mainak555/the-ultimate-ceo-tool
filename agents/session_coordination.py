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
import time
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


# ---------------------------------------------------------------------------
# Human gate quorum — per-user Redis response keys
# ---------------------------------------------------------------------------

def _gate_response_ttl() -> int:
    """Return gate-response key TTL from settings (default 6 h)."""
    raw = int(getattr(settings, "REDIS_GATE_RESPONSE_TTL_SECONDS", 21600) or 21600)
    return max(60, raw)


def _pending_task_ttl() -> int:
    """Return pending-task key TTL from settings (default 5 min)."""
    raw = int(getattr(settings, "REDIS_PENDING_TASK_TTL_SECONDS", 300) or 300)
    return max(60, raw)


def _remote_user_token_ttl() -> int:
    """Return remote-user invitation token TTL from settings (default 6 h)."""
    raw = int(getattr(settings, "REDIS_REMOTE_USER_TOKEN_TTL_SECONDS", 21600) or 21600)
    return max(60, raw)


def _remote_user_online_status_ttl() -> int:
    """Return online status TTL from settings (default 5 min)."""
    raw = int(getattr(settings, "REDIS_REMOTE_USER_ONLINE_STATUS_TTL_SECONDS", 300) or 300)
    return max(60, raw)


def _gate_response_key(session_id: str, responder_name: str, round_number: int) -> str:
    return f"{_namespace()}:gate_response:{session_id}:{round_number}:{responder_name}"


def _gate_winner_key(session_id: str, round_number: int) -> str:
    return f"{_namespace()}:gate_winner:{session_id}:{round_number}"


def _pending_task_key(session_id: str) -> str:
    return f"{_namespace()}:pending_task:{session_id}"


def _team_choice_turn_ttl() -> int:
    """Return team_choice turn/request TTL from settings (default 30 min)."""
    raw = int(getattr(settings, "REDIS_TEAM_CHOICE_TURN_TTL_SECONDS", 1800) or 1800)
    return max(60, raw)


def _team_choice_wait_timeout() -> int:
    """Return max wait duration for a proxy turn response (default 15 min)."""
    raw = int(getattr(settings, "REDIS_TEAM_CHOICE_WAIT_TIMEOUT_SECONDS", 900) or 900)
    return max(30, raw)


def _team_choice_poll_interval() -> float:
    """Return polling interval used while waiting for proxy response."""
    raw = float(getattr(settings, "REDIS_TEAM_CHOICE_POLL_INTERVAL_SECONDS", 1.0) or 1.0)
    return max(0.2, raw)


def _team_choice_active_request_key(session_id: str) -> str:
    return f"{_namespace()}:team_choice:{session_id}:active_request"


def _team_choice_response_key(session_id: str, request_id: str) -> str:
    return f"{_namespace()}:team_choice:{session_id}:request:{request_id}:response"


def _team_choice_claim_key(session_id: str, request_id: str) -> str:
    return f"{_namespace()}:team_choice:{session_id}:request:{request_id}:claimed"


def _team_choice_turn_channel(session_id: str) -> str:
    return f"{_namespace()}:team_choice:{session_id}:turn_events"


@traced_function("agents.session.team_choice_request_set")
def set_team_choice_active_request(
    session_id: str,
    request_id: str,
    proxy_name: str,
    remote_user_name: str,
    round_number: int | None = None,
) -> None:
    """Store the currently active team_choice proxy-input request.

    Publishes a readiness event so host/remote websocket clients can toggle
    input availability for the selected remote participant.
    """
    import json as _json

    payload = {
        "request_id": request_id,
        "proxy_name": proxy_name,
        "remote_user_name": remote_user_name,
        "round": int(round_number) if round_number is not None else None,
        "created_at": int(time.time()),
    }
    ttl = _team_choice_turn_ttl()
    try:
        client = _get_client()
        client.set(
            _team_choice_active_request_key(session_id),
            _json.dumps(payload),
            ex=ttl,
        )
        client.publish(
            _remote_user_pubsub_channel(session_id),
            _json.dumps(
                {
                    "type": "team_choice_turn_requested",
                    "request_id": request_id,
                    "proxy_name": proxy_name,
                    "remote_user_name": remote_user_name,
                    "round": payload["round"],
                }
            ),
        )
        client.publish(
            _team_choice_turn_channel(session_id),
            _json.dumps(
                {
                    "type": "team_choice_turn_requested",
                    "request_id": request_id,
                    "proxy_name": proxy_name,
                    "remote_user_name": remote_user_name,
                    "round": payload["round"],
                }
            ),
        )
        logger.info(
            "agents.session.team_choice_request_set",
            extra={
                "session_id": session_id,
                "request_id": request_id,
                "proxy_name": proxy_name,
                "remote_user_name": remote_user_name,
                "round": payload["round"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set team_choice active request.") from exc


def get_team_choice_active_request(session_id: str) -> dict | None:
    """Return the currently active team_choice request for a session."""
    import json as _json

    try:
        raw = _get_client().get(_team_choice_active_request_key(session_id))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get team_choice active request.") from exc
    if not raw:
        return None
    try:
        payload = _json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001
        return None


@traced_function("agents.session.team_choice_request_clear")
def clear_team_choice_active_request(session_id: str, request_id: str | None = None) -> None:
    """Clear active team_choice request and publish turn-closed event."""
    import json as _json

    ended_payload = {
        "type": "team_choice_turn_resolved",
        "request_id": request_id or "",
    }
    try:
        active = get_team_choice_active_request(session_id)
        if request_id and active and active.get("request_id") != request_id:
            return
        resolved_request_id = request_id or ""
        if active:
            ended_payload["proxy_name"] = active.get("proxy_name", "")
            ended_payload["remote_user_name"] = active.get("remote_user_name", "")
            if not ended_payload["request_id"]:
                ended_payload["request_id"] = active.get("request_id", "")
            if not resolved_request_id:
                resolved_request_id = str(active.get("request_id") or "")
        client = _get_client()
        keys = [_team_choice_active_request_key(session_id)]
        if resolved_request_id:
            keys.append(_team_choice_response_key(session_id, resolved_request_id))
            keys.append(_team_choice_claim_key(session_id, resolved_request_id))
        client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to clear team_choice active request.") from exc

    try:
        msg = _json.dumps(ended_payload)
        client = _get_client()
        client.publish(_remote_user_pubsub_channel(session_id), msg)
        client.publish(_team_choice_turn_channel(session_id), msg)
        logger.info(
            "agents.session.team_choice_request_cleared",
            extra={
                "session_id": session_id,
                "request_id": ended_payload.get("request_id", ""),
                "proxy_name": ended_payload.get("proxy_name", ""),
                "remote_user_name": ended_payload.get("remote_user_name", ""),
            },
        )
    except Exception:  # noqa: BLE001
        pass


@traced_function("agents.session.team_choice_response_submit")
def submit_team_choice_response(
    session_id: str,
    request_id: str,
    responder_name: str,
    text: str,
    attachment_ids: list[str] | None = None,
    text_with_context: str | None = None,
    images: list[dict] | None = None,
) -> bool:
    """Store a remote response for the active team_choice request.

    First submission wins via a per-request claim key (SET NX).
    Returns True when accepted, False when already claimed.
    """
    import json as _json

    claim_key = _team_choice_claim_key(session_id, request_id)
    response_key = _team_choice_response_key(session_id, request_id)
    ttl = _team_choice_turn_ttl()
    payload = {
        "request_id": request_id,
        "responder_name": responder_name,
        "text": text or "",
        "attachment_ids": list(attachment_ids or []),
        "text_with_context": text_with_context if text_with_context is not None else (text or ""),
        "images": list(images or []),
        "timestamp": int(time.time()),
    }

    try:
        client = _get_client()
        claimed = bool(client.set(claim_key, responder_name, ex=ttl, nx=True))
        if not claimed:
            logger.info(
                "agents.session.team_choice_response_conflict",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "responder_name": responder_name,
                },
            )
            return False
        pipe = client.pipeline(transaction=False)
        pipe.set(response_key, _json.dumps(payload), ex=ttl)
        pipe.publish(
            _remote_user_pubsub_channel(session_id),
            _json.dumps(
                {
                    "type": "team_choice_turn_submitted",
                    "request_id": request_id,
                    "responder_name": responder_name,
                }
            ),
        )
        pipe.publish(
            _team_choice_turn_channel(session_id),
            _json.dumps(
                {
                    "type": "team_choice_turn_submitted",
                    "request_id": request_id,
                    "responder_name": responder_name,
                }
            ),
        )
        pipe.execute()
        logger.info(
            "agents.session.team_choice_response_submitted",
            extra={
                "session_id": session_id,
                "request_id": request_id,
                "responder_name": responder_name,
                "attachment_count": len(payload["attachment_ids"]),
                "image_count": len(payload["images"]),
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to submit team_choice response.") from exc


@traced_function("agents.session.team_choice_response_pop")
def pop_team_choice_response(session_id: str, request_id: str) -> dict | None:
    """Atomically consume a team_choice response payload for a request."""
    import json as _json

    response_key = _team_choice_response_key(session_id, request_id)
    try:
        client = _get_client()
        try:
            raw = client.getdel(response_key)
        except AttributeError:
            pipe = client.pipeline(transaction=True)
            pipe.get(response_key)
            pipe.delete(response_key)
            results = pipe.execute()
            raw = results[0]
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to pop team_choice response.") from exc

    if not raw:
        return None
    try:
        payload = _json.loads(raw)
        if isinstance(payload, dict):
            logger.info(
                "agents.session.team_choice_response_popped",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "responder_name": payload.get("responder_name", ""),
                },
            )
        return payload if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001
        return None


@traced_function("agents.session.team_choice_response_wait")
def wait_for_team_choice_response(session_id: str, request_id: str) -> dict | None:
    """Wait until a team_choice response is available, cancelled, or timed out."""
    deadline = time.time() + _team_choice_wait_timeout()
    poll_interval = _team_choice_poll_interval()

    while True:
        if is_cancel_signaled(session_id):
            logger.info(
                "agents.session.team_choice_wait_cancelled",
                extra={"session_id": session_id, "request_id": request_id},
            )
            return None
        response = pop_team_choice_response(session_id, request_id)
        if response is not None:
            logger.info(
                "agents.session.team_choice_wait_completed",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "responder_name": response.get("responder_name", ""),
                },
            )
            return response
        if time.time() >= deadline:
            logger.warning(
                "agents.session.team_choice_wait_timeout",
                extra={"session_id": session_id, "request_id": request_id},
            )
            return None
        time.sleep(poll_interval)


def store_gate_response(
    session_id: str,
    responder_name: str,
    text: str,
    attachment_ids: list,
    round_number: int,
) -> None:
    """Store a gate responder's input in an individual Redis key.

    Key: ``{NS}:gate_response:{session_id}:{round_number}:{responder_name}``
    Each responder owns their own key — no shared hash, no write contention.
    """
    import json as _json

    key = _gate_response_key(session_id, responder_name, round_number)
    payload = _json.dumps({
        "text": text or "",
        "attachment_ids": list(attachment_ids or []),
        "round": round_number,
    })
    try:
        _get_client().set(key, payload, ex=_gate_response_ttl())
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to store gate response.") from exc
    logger.debug(
        "agents.session.gate_response_stored",
        extra={"session_id": session_id, "responder": responder_name},
    )


def get_gate_response(
    session_id: str,
    responder_name: str,
    round_number: int,
) -> dict | None:
    """Return the stored gate response for a single responder, or None if absent."""
    import json as _json

    key = _gate_response_key(session_id, responder_name, round_number)
    try:
        raw = _get_client().get(key)
        return _json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get gate response.") from exc


def check_all_gate_responses(
    session_id: str,
    expected_names: list[str],
    round_number: int,
) -> tuple[bool, dict]:
    """Check whether all expected responders have submitted gate responses.

    Returns ``(all_present, collected)`` where ``collected`` maps
    ``responder_name → {text, attachment_ids}`` for each present responder,
    in the same order as ``expected_names``. Uses a pipelined GET for one
    round-trip regardless of responder count.
    """
    import json as _json

    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        for name in expected_names:
            pipe.get(_gate_response_key(session_id, name, round_number))
        results = pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to check gate responses.") from exc

    collected: dict = {}
    all_present = True
    for name, raw in zip(expected_names, results):
        if raw is None:
            all_present = False
        else:
            try:
                collected[name] = _json.loads(raw)
            except Exception:  # noqa: BLE001
                all_present = False
    return all_present, collected


def claim_gate_winner(session_id: str, claimer_name: str, round_number: int) -> bool:
    """Atomically claim the gate winner role using SET NX.

    Returns ``True`` when this claimer wins the race; ``False`` when another
    caller already claimed (concurrent POST scenario).
    """
    key = _gate_winner_key(session_id, round_number)
    try:
        return bool(_get_client().set(key, claimer_name, ex=_gate_response_ttl(), nx=True))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to claim gate winner.") from exc


def clear_gate_responses(
    session_id: str,
    expected_names: list[str],
    round_number: int,
) -> None:
    """Delete all per-user gate response keys and the winner key.

    Called once quorum is met and the session has been set to idle.
    DEL on non-existent keys is a no-op in Redis — safe to call with a full
    expected_names list even when some users never responded (phase 1 auto-complete).
    """
    keys = [_gate_response_key(session_id, name, round_number) for name in expected_names]
    keys.append(_gate_winner_key(session_id, round_number))
    try:
        _get_client().delete(*keys)
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to clear gate responses.") from exc
    logger.debug(
        "agents.session.gate_responses_cleared",
        extra={"session_id": session_id, "count": len(expected_names)},
    )


def store_pending_task(
    session_id: str,
    task: str,
    attachment_ids: list | None = None,
) -> None:
    """Store a quorum-composed task for the next /run/ call to consume.

    The key is consumed atomically by ``pop_pending_task`` at run start.
    Storing the task here (rather than passing it back via the respond response
    body) prevents a second human discussion entry from being persisted in
    event_stream — the quorum path already inserts ordered per-user entries.
    """
    import json as _json

    key = _pending_task_key(session_id)
    payload = _json.dumps({"task": task or "", "attachment_ids": list(attachment_ids or [])})
    try:
        _get_client().set(key, payload, ex=_pending_task_ttl())
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to store pending task.") from exc


def pop_pending_task(session_id: str) -> dict | None:
    """Atomically get-and-delete the pending quorum task for a session.

    Returns ``{task: str, attachment_ids: list}`` when a pending task exists,
    or ``None`` when no pending task is stored (non-quorum resume).
    Uses GETDEL (Redis 6.2+) with a GET+DEL pipeline fallback for older Redis.
    """
    import json as _json

    key = _pending_task_key(session_id)
    try:
        client = _get_client()
        try:
            raw = client.getdel(key)
        except AttributeError:
            # Redis < 6.2 — fall back to pipeline GET + DEL
            pipe = client.pipeline(transaction=True)
            pipe.get(key)
            pipe.delete(key)
            results = pipe.execute()
            raw = results[0]
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to pop pending task.") from exc

    if raw is None:
        return None
    try:
        return _json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Remote user readiness — invitation tokens, online status, pub/sub
# ---------------------------------------------------------------------------

def _remote_user_token_key(session_id: str, user_name: str) -> str:
    """Forward key: session+name → token."""
    return f"{_namespace()}:remote_user:{session_id}:{user_name}:token"


def _remote_user_token_reverse_key(token: str) -> str:
    """Reverse key: token → {session_id, user_name, project_id}."""
    return f"{_namespace()}:remote_user:token:{token}"


def _remote_user_export_key(session_id: str, user_name: str) -> str:
    """Forward key: session+name → impersonated export key."""
    return f"{_namespace()}:remote_user:{session_id}:{user_name}:export_key"


def _remote_export_reverse_key(export_key: str) -> str:
    """Reverse key: export_key → {session_id, user_name}."""
    return f"{_namespace()}:remote_export:key:{export_key}"


def _remote_user_status_key(session_id: str, user_name: str) -> str:
    return f"{_namespace()}:remote_user:{session_id}:{user_name}:status"


def _remote_user_pubsub_channel(session_id: str) -> str:
    return f"{_namespace()}:remote_user:readiness:{session_id}"


def _guest_pubsub_channel(session_id: str) -> str:
    return f"{_namespace()}:remote_user:{session_id}:guest"


def _session_message_channel(session_id: str) -> str:
    return f"{_namespace()}:session:messages:{session_id}"


def _guest_token_key(session_id: str) -> str:
    return f"{_namespace()}:guest_user:{session_id}:token"


def _guest_token_reverse_key(token: str) -> str:
    return f"{_namespace()}:guest_user:token:{token}"


def _guest_status_key(session_id: str) -> str:
    return f"{_namespace()}:guest_user:{session_id}:status"


def generate_remote_user_token(session_id: str, user_name: str, project_id: str) -> str:
    """Generate a UUID4 invitation token for a remote user and store it in Redis.

    Stores forward key (session+name → token) and reverse key (token → metadata)
    with the configured TTL. Returns the new token.
    """
    import json as _json
    from uuid import uuid4 as _uuid4

    token = str(_uuid4())
    ttl = _remote_user_token_ttl()
    meta = _json.dumps({"session_id": session_id, "user_name": user_name, "project_id": project_id})
    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        pipe.set(_remote_user_token_key(session_id, user_name), token, ex=ttl)
        pipe.set(_remote_user_token_reverse_key(token), meta, ex=ttl)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to generate remote user token.") from exc
    logger.info(
        "agents.remote_user.token_generated",
        extra={"session_id": session_id, "user_name": user_name},
    )
    return token


def generate_remote_user_export_key(session_id: str, user_name: str) -> str:
    """Generate a UUID4 impersonated export key for a remote user and store it in Redis.

    The forward key maps session+name → export_key; the reverse key maps
    export_key → {session_id, user_name}.  Uses the same TTL as remote user tokens.
    Returns the new export key.
    """
    import json as _json
    from uuid import uuid4 as _uuid4

    export_key = str(_uuid4())
    ttl = _remote_user_token_ttl()
    meta = _json.dumps({"session_id": session_id, "user_name": user_name})
    try:
        client = _get_client()
        # Revoke any previously existing export key for this user.
        old = client.get(_remote_user_export_key(session_id, user_name))
        pipe = client.pipeline(transaction=False)
        if old:
            pipe.delete(_remote_export_reverse_key(old))
        pipe.set(_remote_user_export_key(session_id, user_name), export_key, ex=ttl)
        pipe.set(_remote_export_reverse_key(export_key), meta, ex=ttl)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to generate remote user export key.") from exc
    logger.info(
        "agents.remote_user.export_key_generated",
        extra={"session_id": session_id, "user_name": user_name},
    )
    return export_key


def get_remote_user_export_key(session_id: str, user_name: str) -> str | None:
    """Return the current impersonated export key for a remote user, or None."""
    try:
        return _get_client().get(_remote_user_export_key(session_id, user_name))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get remote user export key.") from exc


def get_remote_export_key_data(export_key: str) -> dict | None:
    """Resolve an impersonated export key to its metadata dict, or None if not found."""
    import json as _json

    try:
        raw = _get_client().get(_remote_export_reverse_key(export_key))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get remote export key data.") from exc
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def revoke_remote_user_export_key(session_id: str, user_name: str) -> None:
    """Delete the impersonated export key for a remote user."""
    try:
        client = _get_client()
        export_key = client.get(_remote_user_export_key(session_id, user_name))
        keys = [_remote_user_export_key(session_id, user_name)]
        if export_key:
            keys.append(_remote_export_reverse_key(export_key))
        client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to revoke remote user export key.") from exc
    logger.info(
        "agents.remote_user.export_key_revoked",
        extra={"session_id": session_id, "user_name": user_name},
    )


def get_all_remote_user_export_states(session_id: str, user_names: list[str]) -> dict[str, bool]:
    """Return a mapping of {user_name: has_export_key} for all provided names."""
    if not user_names:
        return {}
    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        for name in user_names:
            pipe.exists(_remote_user_export_key(session_id, name))
        results = pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get remote user export states.") from exc
    return {name: bool(exists) for name, exists in zip(user_names, results)}


def generate_guest_token(session_id: str, project_id: str) -> str:
    """Generate a UUID4 invitation token for guest readonly access."""
    import json as _json
    from uuid import uuid4 as _uuid4

    token = str(_uuid4())
    ttl = _remote_user_token_ttl()
    meta = _json.dumps({"session_id": session_id, "project_id": project_id})
    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        old_token = client.get(_guest_token_key(session_id))
        pipe.set(_guest_token_key(session_id), token, ex=ttl)
        pipe.set(_guest_token_reverse_key(token), meta, ex=ttl)
        if old_token:
            pipe.delete(_guest_token_reverse_key(old_token))
        pipe.delete(_guest_status_key(session_id))
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to generate guest token.") from exc
    logger.info(
        "agents.guest.token_generated",
        extra={"session_id": session_id},
    )
    return token


def revoke_remote_user_token(session_id: str, user_name: str) -> None:
    """Delete both forward and reverse token keys for a remote user."""
    try:
        client = _get_client()
        token = client.get(_remote_user_token_key(session_id, user_name))
        keys = [_remote_user_token_key(session_id, user_name)]
        if token:
            keys.append(_remote_user_token_reverse_key(token))
        client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to revoke remote user token.") from exc


def get_remote_user_token(session_id: str, user_name: str) -> str | None:
    """Return the current invitation token for a remote user, or None if absent."""
    try:
        return _get_client().get(_remote_user_token_key(session_id, user_name))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get remote user token.") from exc


def get_remote_user_token_data(token: str) -> dict | None:
    """Reverse-lookup a token → {session_id, user_name, project_id}, or None."""
    import json as _json

    try:
        raw = _get_client().get(_remote_user_token_reverse_key(token))
        return _json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to lookup remote user token.") from exc


def get_guest_token(session_id: str) -> str | None:
    """Return the active guest token for a session, or None."""
    try:
        return _get_client().get(_guest_token_key(session_id))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get guest token.") from exc


def get_guest_token_data(token: str) -> dict | None:
    """Reverse-lookup a guest token to metadata or None."""
    import json as _json

    try:
        raw = _get_client().get(_guest_token_reverse_key(token))
        return _json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to lookup guest token.") from exc


def get_remote_user_statuses(session_id: str, user_names: list[str]) -> dict[str, str]:
    """Return a mapping of user_name → status ('online'/'offline'/'ignored').

    Defaults to 'offline' for any user with no status key.
    Uses a single pipeline round-trip regardless of user count.
    """
    if not user_names:
        return {}
    try:
        client = _get_client()
        pipe = client.pipeline(transaction=False)
        for name in user_names:
            pipe.get(_remote_user_status_key(session_id, name))
        results = pipe.execute()
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get remote user statuses.") from exc

    return {name: (raw or "offline") for name, raw in zip(user_names, results)}


def set_remote_user_online(session_id: str, user_name: str) -> None:
    """Mark a remote user as online and publish an update event."""
    try:
        client = _get_client()
        client.set(_remote_user_status_key(session_id, user_name), "online",
                   ex=_remote_user_online_status_ttl())
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set remote user online.") from exc

    _publish_remote_user_event(session_id, {
        "type": "update",
        "user_name": user_name,
        "status": "online",
    })
    logger.info(
        "agents.remote_user.online",
        extra={"session_id": session_id, "user_name": user_name},
    )


def set_remote_user_ignored(session_id: str, user_name: str) -> None:
    """Mark a remote user as ignored (host un-checked) and publish an update."""
    try:
        client = _get_client()
        client.set(_remote_user_status_key(session_id, user_name), "ignored",
                   ex=_remote_user_token_ttl())
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set remote user ignored.") from exc

    _publish_remote_user_event(session_id, {
        "type": "update",
        "user_name": user_name,
        "status": "ignored",
    })


def set_remote_user_offline(session_id: str, user_name: str) -> None:
    """Mark a remote user as offline (WebSocket closed)."""
    try:
        client = _get_client()
        client.delete(_remote_user_status_key(session_id, user_name))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set remote user offline.") from exc

    _publish_remote_user_event(session_id, {
        "type": "update",
        "user_name": user_name,
        "status": "offline",
    })


def set_remote_user_offline_if_online(session_id: str, user_name: str) -> bool:
    """Delete status only when current value is 'online'.

    This preserves durable 'ignored' status when the host explicitly un-checks
    a remote participant and the browser then disconnects.
    Returns True when the key changed from online to offline.
    """
    key = _remote_user_status_key(session_id, user_name)
    try:
        client = _get_client()
        current = client.get(key)
        if current != "online":
            return False
        client.delete(key)
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set remote user offline.") from exc

    _publish_remote_user_event(session_id, {
        "type": "update",
        "user_name": user_name,
        "status": "offline",
    })
    return True


def touch_remote_user_online_status(session_id: str, user_name: str) -> bool:
    """Refresh TTL for an online status key.

    Returns True when TTL was refreshed for an "online" status key.
    Returns False when status is absent or not "online".
    """
    key = _remote_user_status_key(session_id, user_name)
    ttl = _remote_user_online_status_ttl()
    try:
        client = _get_client()
        current = client.get(key)
        if current != "online":
            return False
        client.expire(key, ttl)
        return True
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to refresh remote user status TTL.") from exc


def _publish_remote_user_event(session_id: str, payload: dict) -> None:
    """Internal: publish a JSON event to the remote-user pub/sub channel."""
    import json as _json

    try:
        _get_client().publish(
            _remote_user_pubsub_channel(session_id),
            _json.dumps(payload),
        )
    except Exception:  # noqa: BLE001
        pass  # pub/sub failure is non-fatal


def _publish_guest_event(session_id: str, payload: dict) -> None:
    """Internal: publish a JSON event to the guest pub/sub channel."""
    import json as _json

    try:
        _get_client().publish(
            _guest_pubsub_channel(session_id),
            _json.dumps(payload),
        )
    except Exception:  # noqa: BLE001
        pass  # pub/sub failure is non-fatal


def publish_remote_user_event(session_id: str, payload: dict) -> None:
    """Publish a remote-user readiness event (public wrapper for external callers)."""
    _publish_remote_user_event(session_id, payload)


def publish_guest_event(session_id: str, payload: dict) -> None:
    """Publish a guest watcher event (public wrapper for external callers)."""
    _publish_guest_event(session_id, payload)


# ─────────────────────────────────────────────────────────────────────────────
# Per-session quorum override (host may change quorum in the waiting panel)
# ─────────────────────────────────────────────────────────────────────────────

def _remote_user_quorum_key(session_id: str) -> str:
    return f"{_namespace()}:quorum:{session_id}"


def _remote_user_readiness_latch_key(session_id: str) -> str:
    return f"{_namespace()}:remote_user:{session_id}:readiness_latch"


def set_session_quorum(session_id: str, quorum: str) -> None:
    """Store a per-session quorum override (all | first_win | team_choice) with token TTL."""
    try:
        _get_client().set(_remote_user_quorum_key(session_id), quorum,
                          ex=_remote_user_token_ttl())
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set session quorum.") from exc
    logger.info(
        "agents.remote_user.quorum_set",
        extra={"session_id": session_id, "quorum": quorum},
    )


def set_remote_user_readiness_latch(session_id: str, user_name: str) -> None:
    """Set a deferred readiness latch for the next run start.

    This latch is used when a required remote participant disconnects while a run
    is already in progress. The current run is not interrupted; the next run
    start is blocked until readiness is satisfied.
    """
    import json as _json

    key = _remote_user_readiness_latch_key(session_id)
    payload = _json.dumps({"user_name": user_name or ""})
    try:
        _get_client().set(key, payload, ex=_remote_user_token_ttl())
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set remote readiness latch.") from exc


def has_remote_user_readiness_latch(session_id: str) -> bool:
    """Return True when the deferred remote readiness latch is set."""
    key = _remote_user_readiness_latch_key(session_id)
    try:
        return bool(_get_client().exists(key))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to read remote readiness latch.") from exc


def clear_remote_user_readiness_latch(session_id: str) -> None:
    """Clear deferred remote readiness latch for this session."""
    key = _remote_user_readiness_latch_key(session_id)
    try:
        _get_client().delete(key)
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to clear remote readiness latch.") from exc


def get_session_quorum(session_id: str) -> str | None:
    """Return the per-session quorum override, or None if not set."""
    try:
        return _get_client().get(_remote_user_quorum_key(session_id))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to get session quorum.") from exc


def purge_remote_user_session_keys(session_id: str, user_names: list[str]) -> None:
    """Delete all token, status, export, and quorum keys for a session's remote users."""
    keys: list[str] = [
        _remote_user_quorum_key(session_id),
        _remote_user_readiness_latch_key(session_id),
    ]
    try:
        client = _get_client()
        # Collect tokens and export keys so we can delete reverse keys too.
        pipe = client.pipeline(transaction=False)
        for name in user_names:
            pipe.get(_remote_user_token_key(session_id, name))
        for name in user_names:
            pipe.get(_remote_user_export_key(session_id, name))
        results = pipe.execute()
        tokens = results[: len(user_names)]
        export_keys = results[len(user_names) :]

        for name, token in zip(user_names, tokens):
            keys.append(_remote_user_token_key(session_id, name))
            keys.append(_remote_user_status_key(session_id, name))
            if token:
                keys.append(_remote_user_token_reverse_key(token))
        for name, export_key in zip(user_names, export_keys):
            keys.append(_remote_user_export_key(session_id, name))
            if export_key:
                keys.append(_remote_export_reverse_key(export_key))
        client.delete(*keys)
        # Clear team_choice ephemeral keys for this session.
        pattern = f"{_namespace()}:team_choice:{session_id}:*"
        cursor = 0
        while True:
            cursor, tc_keys = client.scan(cursor=cursor, match=pattern, count=100)
            if tc_keys:
                client.delete(*tc_keys)
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to purge remote user session keys.") from exc


def revoke_guest_token(session_id: str) -> None:
    """Delete guest token keys for a session and broadcast eviction."""
    try:
        client = _get_client()
        token = client.get(_guest_token_key(session_id))
        keys = [_guest_token_key(session_id), _guest_status_key(session_id)]
        if token:
            keys.append(_guest_token_reverse_key(token))
        client.delete(*keys)
        _publish_guest_event(session_id, {"type": "evict"})
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to revoke guest token.") from exc


def set_guest_online(session_id: str) -> None:
    """Mark guest watcher online for this session."""
    try:
        _get_client().set(
            _guest_status_key(session_id),
            "online",
            ex=_remote_user_token_ttl(),
        )
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set guest online.") from exc


def set_guest_offline(session_id: str) -> None:
    """Clear guest watcher online marker for this session."""
    try:
        _get_client().delete(_guest_status_key(session_id))
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to set guest offline.") from exc


def purge_guest_session_keys(session_id: str) -> None:
    """Delete guest token and status keys for a session."""
    try:
        client = _get_client()
        token = client.get(_guest_token_key(session_id))
        keys = [_guest_token_key(session_id), _guest_status_key(session_id)]
        if token:
            keys.append(_guest_token_reverse_key(token))
        client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        raise SessionCoordinationError("Unable to purge guest session keys.") from exc


def publish_session_message(session_id: str, message: dict) -> None:
    """Publish a chat message event to the session message channel.

    Remote-user WebSocket consumers subscribe to this channel to receive
    live agent messages without polling.
    """
    import json as _json

    try:
        _get_client().publish(
            _session_message_channel(session_id),
            _json.dumps(message),
        )
    except Exception:  # noqa: BLE001
        pass  # pub/sub failure is non-fatal


__all__ = [
    "SessionCoordinationError",
    "acquire_run_lease",
    "check_all_gate_responses",
    "claim_gate_winner",
    "clear_cancel_signal",
    "clear_gate_responses",
    "clear_run_traceparent",
    "delete_mcp_oauth_readiness",
    "ensure_redis_available",
    "generate_remote_user_token",
    "generate_guest_token",
    "get_gate_response",
    "get_heartbeat_interval_seconds",
    "get_instance_id",
    "get_mcp_oauth_authorized_count",
    "get_mcp_oauth_test_status",
    "get_mcp_oauth_token",
    "get_and_delete_mcp_oauth_state",
    "get_redis_client",
    "get_remote_user_statuses",
    "get_remote_user_token",
    "get_remote_user_token_data",
    "get_remote_user_export_key",
    "get_remote_export_key_data",
    "get_all_remote_user_export_states",
    "generate_remote_user_export_key",
    "revoke_remote_user_export_key",
    "get_guest_token",
    "get_guest_token_data",
    "get_run_traceparent",
    "init_mcp_oauth_readiness",
    "is_cancel_signaled",
    "list_authorized_oauth_servers",
    "pop_pending_task",
    "publish_oauth_server_authorized",
    "publish_remote_user_event",
    "publish_guest_event",
    "publish_session_message",
    "purge_mcp_oauth_tokens",
    "purge_remote_user_session_keys",
    "purge_guest_session_keys",
    "release_run_lease",
    "renew_run_lease",
    "revoke_remote_user_token",
    "revoke_guest_token",
    "set_mcp_oauth_state",
    "set_mcp_oauth_test_status",
    "set_mcp_oauth_token",
    "set_remote_user_ignored",
    "set_remote_user_offline",
    "set_remote_user_offline_if_online",
    "set_remote_user_online",
    "set_guest_online",
    "set_guest_offline",
    "set_session_quorum",
    "get_session_quorum",
    "set_remote_user_readiness_latch",
    "has_remote_user_readiness_latch",
    "clear_remote_user_readiness_latch",
    "signal_cancel",
    "submit_team_choice_response",
    "store_gate_response",
    "store_pending_task",
    "store_run_traceparent",
    "set_team_choice_active_request",
    "get_team_choice_active_request",
    "clear_team_choice_active_request",
    "pop_team_choice_response",
    "wait_for_team_choice_response",
    "touch_remote_user_online_status",
]
