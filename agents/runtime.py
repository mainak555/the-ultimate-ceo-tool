"""
In-memory AutoGen team runtime cache.

Teams are keyed by session_id and kept alive between SSE calls so AutoGen's
built-in conversation history is preserved for multi-round human-gated runs.

Cache is process-local. On server restart a session in "awaiting_input" will
rebuild the team; the full prior discussion from MongoDB is passed as the
initial task so agents retain context.

## Horizontal scaling constraint

_TEAM_CACHE and _CANCEL_TOKENS are process-local dicts. AutoGen team objects
(RoundRobinGroupChat / SelectorGroupChat) hold live asyncio tasks, agent
instances, and MCP workbench connections — they cannot be serialised to Redis
or any shared store. Therefore:

  * Cross-instance run ownership and cancel signalling use Redis exclusively
    via agents/session_coordination.py (lease key + cancel key). The team
    object stays in-process.
  * Multi-replica deployments MUST enable session-affinity (sticky sessions)
    at the load balancer / ingress so that every SSE stream and HITL resume
    request for a given session_id is routed to the same container instance.
  * Crash / restart recovery: if the owning container dies the session is
    rebuilt on the next request (cache miss). MongoDB agent_state provides
    the durable checkpoint for load_state() restore; the Redis lease is
    expired by TTL so the new instance can acquire ownership.
  * Memcached or Redis cannot replace this cache — they cannot hold live
    Python objects with open asyncio tasks or network sockets.

See docs/agent_teams.md §"Horizontal Scaling" and the relevant deployment
README for ingress / load-balancer sticky-session configuration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autogen_agentchat.conditions import ExternalTermination
    from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat
    from autogen_core import CancellationToken

logger = logging.getLogger(__name__)

# session_id → team instance
_TEAM_CACHE: dict[str, "RoundRobinGroupChat | SelectorGroupChat"] = {}

# session_id → CancellationToken
_CANCEL_TOKENS: dict[str, "CancellationToken"] = {}

# session_id → ExternalTermination (gated runs only; graceful stop signal)
_EXTERNAL_TERMINATIONS: dict[str, "ExternalTermination"] = {}


def get_or_build_team(
    session_id: str,
    project: dict,
) -> tuple["RoundRobinGroupChat | SelectorGroupChat", "CancellationToken", bool]:
    """
    Return (team, cancellation_token, cache_miss) for session_id.

    Builds a fresh team on cache miss. Existing teams retain AutoGen's
    internal conversation history for stateful resumption.
    """
    from autogen_core import CancellationToken as CT
    from .team_builder import build_team

    cache_miss = session_id not in _TEAM_CACHE
    if cache_miss:
        logger.info("agents.team.cache_miss", extra={"session_id": session_id})
        team = build_team(project)
        _TEAM_CACHE[session_id] = team
        _CANCEL_TOKENS[session_id] = CT()
        # Track MCP workbenches for deterministic teardown on evict_team()
        from .mcp_tools import register_session_workbenches
        wbs = (project.get("_runtime") or {}).get("mcp_workbenches") or []
        register_session_workbenches(session_id, wbs)
        # Track ExternalTermination for graceful Stop (gated runs only)
        ext_stop = (project.get("_runtime") or {}).get("external_termination")
        if ext_stop is not None:
            _EXTERNAL_TERMINATIONS[session_id] = ext_stop
    else:
        logger.debug("agents.team.cache_hit", extra={"session_id": session_id})

    return _TEAM_CACHE[session_id], _CANCEL_TOKENS[session_id], cache_miss


async def save_team_state(team: Any) -> dict:
    """Return serialized AutoGen team state for persistence."""
    return await team.save_state()


async def load_team_state(team: Any, state: dict) -> None:
    """Load previously serialized AutoGen team state into a team instance."""
    await team.load_state(state)


def reset_cancel_token(session_id: str) -> "CancellationToken":
    """Replace the cancellation token (needed between rounds).

    ExternalTermination reset is handled automatically by AutoGen when the
    termination condition fires — no manual reset required here.
    """
    from autogen_core import CancellationToken as CT

    token = CT()
    _CANCEL_TOKENS[session_id] = token
    return token


def cancel_team(session_id: str) -> None:
    """Signal the running SSE stream to stop.

    For human-gated runs, calls ExternalTermination.set() first so the
    current agent turn finishes cleanly and its message is persisted before
    TaskResult is yielded. CancellationToken.cancel() follows as a hard
    fallback to interrupt mid-LLM-call if needed.
    """
    logger.info("agents.team.cancelled", extra={"session_id": session_id})
    # Graceful stop — let current agent turn complete before terminating
    ext_stop = _EXTERNAL_TERMINATIONS.get(session_id)
    if ext_stop is not None:
        ext_stop.set()
    # Hard fallback — interrupts mid-call (also powers cross-instance Redis cancel)
    token = _CANCEL_TOKENS.get(session_id)
    if token:
        token.cancel()


def evict_team(session_id: str) -> None:
    """Remove team from cache (call after stop or final completion)."""
    if session_id in _TEAM_CACHE:
        logger.info("agents.team.evicted", extra={"session_id": session_id})
    _TEAM_CACHE.pop(session_id, None)
    _CANCEL_TOKENS.pop(session_id, None)
    _EXTERNAL_TERMINATIONS.pop(session_id, None)
    # Tear down any MCP workbenches that were attached to this session.
    try:
        from .mcp_tools import close_session_workbenches
        close_session_workbenches(session_id)
    except Exception:  # noqa: BLE001
        logger.exception("agents.mcp.failed", extra={"session_id": session_id, "phase": "evict"})


def evict_all_teams() -> None:
    """Evict all cached teams and trigger MCP teardown for each session."""
    session_ids = set(_TEAM_CACHE.keys()) | set(_CANCEL_TOKENS.keys())
    for session_id in list(session_ids):
        evict_team(session_id)
