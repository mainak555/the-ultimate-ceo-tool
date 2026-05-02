"""Helpers for building AutoGen teams from saved configuration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage

from autogen_agentchat.base import TerminationCondition
from autogen_agentchat.messages import BaseChatMessage as _BaseChatMessage, StopMessage

from .factory import build_model_client
from .mcp_tools import build_mcp_workbenches, resolve_mcp_servers_for_agent
from .prompt_builder import resolve_system_prompt

logger = logging.getLogger(__name__)


def _sanitize_identifier(raw: str, fallback: str) -> str:
    """Return a Python-identifier-safe token."""
    safe = re.sub(r"[\s\-]+", "_", raw or "")
    safe = re.sub(r"[^\w]", "", safe)
    if safe and safe[0].isdigit():
        safe = "_" + safe
    return safe or fallback


def _build_remote_user_proxies(project: dict) -> list:
    """Build non-blocking UserProxyAgent participants for configured remote users.

    Remote users are represented as team participants so selector/roles context
    can include them, but live human input still follows the existing gate flow
    over WebSocket + Redis. The proxy input function is intentionally
    non-blocking to avoid stalling an active run when no inline human input is
    expected.
    """
    gate = project.get("human_gate") or {}
    if not gate.get("enabled"):
        return []

    proxies = []
    used_names: set[str] = set()
    for idx, entry in enumerate(gate.get("remote_users") or []):
        if not isinstance(entry, dict):
            continue
        uid = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        remote_description = str(entry.get("description") or "").strip()
        if not uid or not name:
            continue

        base_name = _sanitize_identifier(uid, f"remote_user_{idx}")
        safe_name = base_name
        suffix = 2
        while safe_name in used_names:
            safe_name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(safe_name)

        async def _proxy_input(_prompt: str, _token=None, _uid=uid) -> str:
            # Non-blocking adapter: remote replies are collected via the
            # existing Human Gate flow and replayed on leader continue.
            return (
                f"Remote participant {_uid} response is collected through "
                "the Human Gate panel."
            )

        from autogen_agentchat.agents import UserProxyAgent

        proxies.append(
            UserProxyAgent(
                name=safe_name,
                description=(
                    f"Remote participant '{name}'. This proxy exists for team "
                    "awareness; do not block on inline input during a run."
                    + (
                        f" Context: {remote_description}"
                        if remote_description
                        else ""
                    )
                ),
                input_func=_proxy_input,
            )
        )
    return proxies


class AgentMessageTermination(TerminationCondition):
    """Terminate after N messages from non-user sources (agents only).

    AutoGen's built-in MaxMessageTermination counts every BaseChatMessage,
    including the initial TextMessage(source="user") task. That causes an
    off-by-one: a limit of N leaves only N-1 agent turns available per round.

    This condition counts only messages where source != "user", so the limit
    maps exactly to the number of agent turns regardless of whether the round
    starts with a user task or a bare resume (task=None).

    Inherits from TerminationCondition so that the native ``__or__`` /
    ``__and__`` operators return proper ``OrTerminationCondition`` /
    ``AndTerminationCondition`` instances with correct static types.
    """

    component_config_schema = None  # type: ignore[assignment]
    component_type = "termination"

    def __init__(self, max_agent_messages: int) -> None:
        self._max = max_agent_messages
        self._count = 0

    @property
    def terminated(self) -> bool:
        return self._count >= self._max

    async def __call__(
        self,
        messages: "Sequence[BaseAgentEvent | BaseChatMessage]",
    ) -> StopMessage | None:
        from autogen_agentchat.base import TerminatedException

        if self.terminated:
            raise TerminatedException("Termination condition has already been reached")
        self._count += sum(
            1
            for m in messages
            if isinstance(m, _BaseChatMessage) and m.source != "user"
        )
        if self._count >= self._max:
            return StopMessage(
                content=f"Agent message limit {self._max} reached, count: {self._count}",
                source="AgentMessageTermination",
            )
        return None

    async def reset(self) -> None:
        self._count = 0

    def _to_config(self) -> dict:  # type: ignore[override]
        return {"max_agent_messages": self._max}

    @classmethod
    def _from_config(cls, config: dict) -> "AgentMessageTermination":  # type: ignore[override]
        return cls(max_agent_messages=config.get("max_agent_messages", 1))


def build_agent_runtime_spec(
    agent_config: dict,
    project: dict | None = None,
    objective: str = "",
    session_id: str | None = None,
) -> dict:
    """Return a lightweight runtime spec for a configured assistant agent."""
    system_message = resolve_system_prompt(
        agent_config.get("system_prompt", ""), objective=objective
    )
    # Extract description from line 1 of the resolved system message so that
    # SelectorGroupChat's {roles} placeholder renders a meaningful summary.
    description = system_message.splitlines()[0].strip() if system_message else ""

    # Resolve MCP tools for this agent (none/shared/dedicated)
    scope = (agent_config.get("mcp_tools") or "none").strip().lower()
    workbenches: list = []
    if scope in ("shared", "dedicated") and project is not None:
        servers = resolve_mcp_servers_for_agent(agent_config, project)
        secrets = project.get("mcp_secrets") or {}
        oauth_configs = project.get("mcp_oauth_configs") or {}
        workbenches = build_mcp_workbenches(
            servers,
            scope=scope,
            secrets=secrets,
            session_id=session_id,
            oauth_configs=oauth_configs,
        )

    return {
        "name": agent_config["name"],
        "model_client": build_model_client(
            agent_config["model"],
            temperature=agent_config.get("temperature", 0.6),
        ),
        "system_message": system_message,
        "description": description,
        "workbenches": workbenches,
    }


def build_team(project: dict, session_id: str | None = None):
    """
    Build an AutoGen team from a normalized project config.

    Team type is read from project["team"]["type"]:
      - "round_robin"  → RoundRobinGroupChat
      - "selector"     → SelectorGroupChat with a dedicated selector_model client

    Termination strategy (both team types):
      - human_gate disabled → AgentMessageTermination(n_agents × max_iterations)
        Runs all rounds automatically; agents only (no user-message off-by-one).
      - human_gate enabled  → AgentMessageTermination(n_agents) | ExternalTermination()
        Stops after one full agent round OR when the Stop button fires .set().
        ExternalTermination instance is stashed in project["_runtime"]["external_termination"]
        so runtime.py can call .set() on it when stop is requested.
    """
    from autogen_agentchat.agents import AssistantAgent

    objective = project.get("objective", "")
    team_cfg = project.get("team", {})
    team_type = (team_cfg.get("type") or "round_robin").strip()

    assistants = []
    all_workbenches: list = []
    mcp_agent_count = 0
    for agent_cfg in project["agents"]:
        spec = build_agent_runtime_spec(
            agent_cfg, project=project, objective=objective, session_id=session_id
        )
        # Ensure name is a valid Python identifier (safety net for legacy docs)
        safe_name = _sanitize_identifier(spec["name"], f"agent_{len(assistants)}")
        agent_kwargs = {
            "name": safe_name,
            "model_client": spec["model_client"],
            "system_message": spec["system_message"],
            "description": spec["description"],
        }
        wbs = spec.get("workbenches") or []
        if wbs:
            agent_kwargs["workbench"] = wbs if len(wbs) > 1 else wbs[0]
            # reflect_on_tool_use: after every tool call the agent makes a
            # second LLM call to synthesise results into a TextMessage.
            # Without this, AutoGen returns a raw ToolCallSummaryMessage —
            # which the SSE handler does not render and which suppresses the
            # assistant LLM output from traces (no second LLMCallEvent).
            agent_kwargs["reflect_on_tool_use"] = True
            all_workbenches.extend(wbs)
            mcp_agent_count += 1
        assistants.append(AssistantAgent(**agent_kwargs))

    remote_proxies = _build_remote_user_proxies(project)
    participants = list(assistants) + list(remote_proxies)

    # Stash workbenches on the team so runtime can register them after build.
    project.setdefault("_runtime", {})["mcp_workbenches"] = all_workbenches

    has_gate = project.get("human_gate", {}).get("enabled", False)
    assistant_count = len(assistants)
    max_iter = team_cfg.get("max_iterations", 5)

    if has_gate:
        from autogen_agentchat.conditions import ExternalTermination
        external_stop = ExternalTermination()
        # Stash so runtime.cancel_team() can call .set() for a graceful stop
        project.setdefault("_runtime", {})["external_termination"] = external_stop
        # Keep existing HITL cadence: one assistant round per run. Remote
        # proxies are represented in the team, but turn-taking remains
        # assistant-driven unless the application explicitly routes otherwise.
        termination = AgentMessageTermination(assistant_count) | external_stop
    else:
        termination = AgentMessageTermination(assistant_count * max_iter)

    if team_type == "selector":
        from autogen_agentchat.teams import SelectorGroupChat

        if assistant_count < 2:
            raise ValueError(
                "Selector team type requires at least 2 assistant agents."
            )

        selector_model_name = team_cfg.get("model", "")
        selector_model_client = build_model_client(
            selector_model_name, temperature=team_cfg.get("temperature", 0.0)
        )

        raw_selector_prompt = team_cfg.get("system_prompt", "")
        # Prepend objective so routing is grounded in the project goal
        if objective:
            selector_prompt = (
                f"Project Objective:\n{objective}\n\n{raw_selector_prompt}"
            )
        else:
            selector_prompt = raw_selector_prompt

        allow_repeated = team_cfg.get("allow_repeated_speaker", True)

        if remote_proxies:
            selector_prompt += (
                "\n\nRemote participants are represented as UserProxyAgent "
                "entries for roster/context awareness. During live runs, do "
                "not route speaker selection to remote proxy participants; "
                "remote responses are collected by the Human Gate flow."
            )

        logger.info(
            "agents.team.built",
            extra={
                "team_type": "selector",
                "agent_count": assistant_count,
                "max_iterations": max_iter,
                "human_gate": has_gate,
                "selector_model": selector_model_name,
                "mcp_agent_count": mcp_agent_count,
                "remote_user_proxy_count": len(remote_proxies),
            },
        )
        return SelectorGroupChat(
            participants,
            model_client=selector_model_client,
            termination_condition=termination,
            selector_prompt=selector_prompt,
            allow_repeated_speaker=allow_repeated,
        )

    # Default: round_robin
    from autogen_agentchat.teams import RoundRobinGroupChat

    logger.info(
        "agents.team.built",
        extra={
            "team_type": "round_robin",
            "agent_count": assistant_count,
            "max_iterations": max_iter,
            "human_gate": has_gate,
            "mcp_agent_count": mcp_agent_count,
            "remote_user_proxy_count": len(remote_proxies),
        },
    )
    return RoundRobinGroupChat(participants, termination_condition=termination)

