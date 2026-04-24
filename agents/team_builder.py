"""Helpers for building AutoGen teams from saved configuration."""

from __future__ import annotations

import logging

from .factory import build_model_client
from .prompt_builder import resolve_system_prompt

logger = logging.getLogger(__name__)


def build_agent_runtime_spec(agent_config: dict, objective: str = "") -> dict:
    """Return a lightweight runtime spec for a configured assistant agent."""
    system_message = resolve_system_prompt(
        agent_config.get("system_prompt", ""), objective=objective
    )
    # Extract description from line 1 of the resolved system message so that
    # SelectorGroupChat's {roles} placeholder renders a meaningful summary.
    description = system_message.splitlines()[0].strip() if system_message else ""
    return {
        "name": agent_config["name"],
        "model_client": build_model_client(
            agent_config["model"],
            temperature=agent_config.get("temperature", 0.6),
        ),
        "system_message": system_message,
        "description": description,
    }


def build_team(project: dict):
    """
    Build an AutoGen team from a normalized project config.

    Team type is read from project["team"]["type"]:
      - "round_robin"  → RoundRobinGroupChat
      - "selector"     → SelectorGroupChat with a dedicated selector_model client

    Termination strategy (both team types):
      - human_gate disabled → MaxMessageTermination(n_agents × max_iterations)
      - human_gate enabled  → MaxMessageTermination(n_agents)
        Stops after one full round; caller calls run_stream() again per round.
    """
    import re

    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.conditions import MaxMessageTermination

    objective = project.get("objective", "")
    team_cfg = project.get("team", {})
    team_type = (team_cfg.get("type") or "round_robin").strip()

    agents = []
    for agent_cfg in project["agents"]:
        spec = build_agent_runtime_spec(agent_cfg, objective=objective)
        # Ensure name is a valid Python identifier (safety net for legacy docs)
        safe_name = re.sub(r"[\s\-]+", "_", spec["name"])
        safe_name = re.sub(r"[^\w]", "", safe_name)
        if safe_name and safe_name[0].isdigit():
            safe_name = "_" + safe_name
        if not safe_name:
            safe_name = f"agent_{len(agents)}"
        agents.append(
            AssistantAgent(
                name=safe_name,
                model_client=spec["model_client"],
                system_message=spec["system_message"],
                description=spec["description"],
            )
        )

    has_gate = project.get("human_gate", {}).get("enabled", False)
    n_agents = len(agents)
    max_iter = team_cfg.get("max_iterations", 5)

    n_messages = n_agents if has_gate else n_agents * max_iter
    termination = MaxMessageTermination(n_messages)

    if team_type == "selector":
        from autogen_agentchat.teams import SelectorGroupChat

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
        },
    )
    return RoundRobinGroupChat(agents, termination_condition=termination)

