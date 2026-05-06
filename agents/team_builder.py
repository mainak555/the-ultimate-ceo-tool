"""Helpers for building AutoGen teams from saved configuration."""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage

from autogen_agentchat.base import TerminationCondition
from autogen_agentchat.messages import BaseChatMessage as _BaseChatMessage, StopMessage

from server.util import sanitize_identifier

from .factory import build_model_client
from .mcp_tools import build_mcp_workbenches, resolve_mcp_servers_for_agent
from .prompt_builder import resolve_system_prompt

logger = logging.getLogger(__name__)


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


def build_team(project: dict, session_id: str | None = None, remote_users: list | None = None):
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

    agents = []
    all_workbenches: list = []
    mcp_agent_count = 0
    for agent_cfg in project["agents"]:
        spec = build_agent_runtime_spec(
            agent_cfg, project=project, objective=objective, session_id=session_id
        )
        # Ensure name is a valid Python identifier (safety net for legacy docs)
        try:
            safe_name = sanitize_identifier(spec["name"], "Agent name")
        except ValueError:
            safe_name = f"agent_{len(agents)}"
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
        agents.append(AssistantAgent(**agent_kwargs))

    # Add UserProxyAgent placeholders for team_choice quorum.
    # Each proxy blocks until the assigned remote user submits text/files.
    # The payload is delivered via Redis coordination helpers.
    proxy_count = 0
    if (project.get("human_gate") or {}).get("quorum") == "team_choice" and remote_users:
        from autogen_agentchat.agents import UserProxyAgent
        from agents.session_coordination import (
            SessionCoordinationError,
            clear_team_choice_active_request,
            set_team_choice_active_request,
            wait_for_team_choice_response,
        )

        def _make_input_func(proxy_name: str, remote_user_name: str):
            async def _placeholder(prompt: str) -> str:
                del prompt  # prompt text is not used by the current remote UI.
                if not session_id:
                    return "Continue."

                request_id = str(uuid4())
                round_number = int(project.get("_current_round") or 0) or None
                try:
                    await asyncio.to_thread(
                        set_team_choice_active_request,
                        session_id,
                        request_id,
                        proxy_name,
                        remote_user_name,
                        round_number,
                    )
                    response = await asyncio.to_thread(
                        wait_for_team_choice_response,
                        session_id,
                        request_id,
                    )
                except SessionCoordinationError:
                    logger.exception(
                        "agents.proxy.turn_request_failed",
                        extra={"session_id": session_id, "proxy": proxy_name},
                    )
                    return "Continue."
                finally:
                    try:
                        await asyncio.to_thread(
                            clear_team_choice_active_request,
                            session_id,
                            request_id,
                        )
                    except SessionCoordinationError:
                        pass

                if not response:
                    logger.info(
                        "agents.proxy.turn_timeout_or_cancel",
                        extra={"session_id": session_id, "proxy": proxy_name},
                    )
                    return "Continue."

                task_text = (response.get("task_text") or "").strip()
                if task_text:
                    return task_text

                text = (response.get("text") or "").strip()
                if text:
                    return text

                if response.get("attachment_ids"):
                    return "Attached files provided."
                return "Continue."
            return _placeholder

        for ru in remote_users:
            raw_name = ru.get("name") or ""
            try:
                safe_proxy_name = sanitize_identifier(raw_name, "Remote user name")
            except ValueError:
                safe_proxy_name = f"remote_user_{proxy_count}"
            agents.append(
                UserProxyAgent(
                    name=safe_proxy_name,
                    description=ru.get("description") or "Remote participant",
                    input_func=_make_input_func(safe_proxy_name, raw_name),
                )
            )
            proxy_count += 1

    # Stash workbenches on the team so runtime can register them after build.
    project.setdefault("_runtime", {})["mcp_workbenches"] = all_workbenches

    has_gate = project.get("human_gate", {}).get("enabled", False)
    n_agents = len(agents)
    max_iter = team_cfg.get("max_iterations", 5)

    if has_gate:
        from autogen_agentchat.conditions import ExternalTermination
        external_stop = ExternalTermination()
        # Stash so runtime.cancel_team() can call .set() for a graceful stop
        project.setdefault("_runtime", {})["external_termination"] = external_stop
        termination = AgentMessageTermination(n_agents) | external_stop
    else:
        termination = AgentMessageTermination(n_agents * max_iter)

    if team_type == "selector":
        from autogen_agentchat.teams import SelectorGroupChat

        if n_agents < 2:
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

        logger.info(
            "agents.team.built",
            extra={
                "team_type": "selector",
                "agent_count": n_agents,
                "max_iterations": max_iter,
                "human_gate": has_gate,
                "selector_model": selector_model_name,
                "mcp_agent_count": mcp_agent_count,
                "proxy_count": proxy_count,
            },
        )
        return SelectorGroupChat(
            agents,
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
            "agent_count": n_agents,
            "max_iterations": max_iter,
            "human_gate": has_gate,
            "mcp_agent_count": mcp_agent_count,
            "proxy_count": proxy_count,
        },
    )
    return RoundRobinGroupChat(agents, termination_condition=termination)

