"""
In-memory AutoGen team runtime cache.

Teams are keyed by session_id and kept alive between SSE calls so AutoGen's
built-in conversation history is preserved for multi-round human-gated runs.

Cache is process-local. On server restart a session in "awaiting_input" will
rebuild the team; the full prior discussion from MongoDB is passed as the
initial task so agents retain context.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat
    from autogen_core import CancellationToken

logger = logging.getLogger(__name__)

# session_id → team instance
_TEAM_CACHE: dict[str, "RoundRobinGroupChat | SelectorGroupChat"] = {}

# session_id → CancellationToken
_CANCEL_TOKENS: dict[str, "CancellationToken"] = {}


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
        _TEAM_CACHE[session_id] = build_team(project)
        _CANCEL_TOKENS[session_id] = CT()
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
    """Replace the cancellation token (needed between rounds)."""
    from autogen_core import CancellationToken as CT

    token = CT()
    _CANCEL_TOKENS[session_id] = token
    return token


def cancel_team(session_id: str) -> None:
    """Signal the currently-running SSE stream to stop after this agent's turn."""
    token = _CANCEL_TOKENS.get(session_id)
    if token:
        logger.info("agents.team.cancelled", extra={"session_id": session_id})
        token.cancel()


def evict_team(session_id: str) -> None:
    """Remove team from cache (call after stop or final completion)."""
    if session_id in _TEAM_CACHE:
        logger.info("agents.team.evicted", extra={"session_id": session_id})
    _TEAM_CACHE.pop(session_id, None)
    _CANCEL_TOKENS.pop(session_id, None)
