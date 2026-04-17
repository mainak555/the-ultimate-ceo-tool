"""Input validation for project configuration data."""

from .model_catalog import get_agent_model_names

TEAM_TYPES = ("round_robin",)
HUMAN_GATE_INTERACTION_MODES = ("approve_reject", "feedback")


def validate_agent(data):
    """Validate and clean a single assistant agent dict."""
    if not isinstance(data, dict):
        raise ValueError("Agent must be a JSON object.")

    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Agent 'name' is required.")

    model = (data.get("model") or data.get("model_name") or "").strip()
    available_models = get_agent_model_names()
    if not model:
        raise ValueError(f"Agent '{name}': 'model' is required.")
    if model not in available_models:
        raise ValueError(
            f"Agent '{name}': 'model' must be one of {', '.join(available_models)}."
        )

    system_prompt = (data.get("system_prompt") or "").strip()
    if not system_prompt:
        raise ValueError(f"Agent '{name}': 'system_prompt' is required.")

    raw_temperature = data.get("temperature", 0.7)
    try:
        temperature = float(raw_temperature)
        if not (0.0 <= temperature <= 2.0):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(
            f"Agent '{name}': 'temperature' must be a number between 0 and 2."
        )

    return {
        "name": name,
        "model": model,
        "system_prompt": system_prompt,
        "temperature": temperature,
    }


def validate_human_gate(data):
    """Validate and clean the optional human gate configuration."""
    if not isinstance(data, dict):
        return {
            "enabled": False,
            "name": "",
            "interaction_mode": "approve_reject",
        }

    enabled = bool(data.get("enabled", False))
    name = (data.get("name") or "").strip()
    interaction_mode = (data.get("interaction_mode") or "approve_reject").strip()

    if interaction_mode not in HUMAN_GATE_INTERACTION_MODES:
        raise ValueError(
            "'human_gate.interaction_mode' must be 'approve_reject' or 'feedback'."
        )

    if enabled and not name:
        raise ValueError("'human_gate.name' is required when human gate is enabled.")

    if not enabled:
        name = ""
        interaction_mode = "approve_reject"

    return {
        "enabled": enabled,
        "name": name,
        "interaction_mode": interaction_mode,
    }


def validate_team(data, human_gate_enabled):
    """Validate and clean team configuration."""
    if not isinstance(data, dict):
        data = {}

    team_type = (data.get("type") or "round_robin").strip()
    if team_type not in TEAM_TYPES:
        raise ValueError(f"'team.type' must be one of {TEAM_TYPES}.")

    max_iterations = data.get("max_iterations", 5)
    try:
        max_iterations = int(max_iterations)
        if max_iterations < 1:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("'team.max_iterations' must be a positive integer.")

    if not human_gate_enabled and max_iterations > 10:
        raise ValueError(
            "'team.max_iterations' cannot be greater than 10 when human gate is disabled."
        )

    return {
        "type": team_type,
        "max_iterations": max_iterations,
    }


def validate_chat_session(data):
    """Validate and clean a chat session creation payload."""
    if not isinstance(data, dict):
        raise ValueError("Session data must be a JSON object.")

    project_id = (data.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("'project_id' is required.")

    description = (data.get("description") or "").strip()
    if not description:
        raise ValueError("'description' is required.")

    if len(description) > 150:
        description = description[:150]

    return {
        "project_id": project_id,
        "description": description,
    }


def validate_project(data):
    """Validate and clean a full project settings dict."""
    if not isinstance(data, dict):
        raise ValueError("Project data must be a JSON object.")

    project_name = (data.get("project_name") or "").strip()
    if not project_name:
        raise ValueError("'project_name' is required.")

    objective = (data.get("objective") or "").strip()
    if not objective:
        raise ValueError("'objective' is required.")

    raw_agents = data.get("agents")
    if not isinstance(raw_agents, list) or len(raw_agents) == 0:
        raise ValueError("At least one assistant agent is required.")

    agents = [validate_agent(agent) for agent in raw_agents]
    agent_names = [agent["name"].lower() for agent in agents]
    if len(agent_names) != len(set(agent_names)):
        raise ValueError("Assistant agent names must be unique.")

    human_gate = validate_human_gate(data.get("human_gate") or {})
    team = validate_team(data.get("team") or {}, human_gate["enabled"])

    return {
        "project_name": project_name,
        "objective": objective,
        "agents": agents,
        "human_gate": human_gate,
        "team": team,
    }
