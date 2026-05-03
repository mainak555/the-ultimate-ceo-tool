"""Input validation for project configuration data."""

import json
import re
from datetime import timezone as _timezone

from .model_catalog import get_agent_model_names
from .util import sanitize_identifier

TEAM_TYPES = ("round_robin", "selector")
JIRA_TYPES = ("software", "service_desk", "business")
MCP_TOOL_SCOPES = ("none", "shared", "dedicated")
MCP_HTTP_TRANSPORT = "http"
MCP_DEPRECATED_TRANSPORTS = ("sse",)

# OAuth 2.0 Authorization Code (+ PKCE) support for HTTP MCP servers.
# mcp_oauth_configs is project-level: {server_name: {auth_url, token_url, client_id, client_secret, scopes?}}
# server_name keys must match actual mcpServers keys (cross-validated in validate_project).
MCP_OAUTH_REQUIRED_FIELDS = ("auth_url", "token_url", "client_id", "client_secret")

# MCP secret keys are UPPER_SNAKE identifiers; placeholders use {KEY_NAME}
# inside any string value of mcp_configuration / shared_mcp_tools and are
# resolved at runtime in agents/mcp_tools.py.
MCP_SECRET_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
MCP_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def _coerce_mcp_dict(raw, label):
    """Accept dict OR JSON string; return a dict. Empty/whitespace string → {}."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}: must be valid JSON ({exc.msg} at line {exc.lineno}).")
        if not isinstance(parsed, dict):
            raise ValueError(f"{label}: top-level value must be a JSON object.")
        return parsed
    raise ValueError(f"{label}: must be a JSON object or JSON string.")


def _validate_mcp_server_entry(name, entry, label):
    """Validate a single mcpServers[<name>] entry. Supports stdio + streamable HTTP."""
    if not isinstance(entry, dict):
        raise ValueError(f"{label}.mcpServers['{name}']: must be a JSON object.")

    transport = (entry.get("transport") or "").strip().lower()
    if transport in MCP_DEPRECATED_TRANSPORTS:
        raise ValueError(
            f"{label}.mcpServers['{name}']: transport '{transport}' is deprecated by MCP. "
            "Use stdio (default) or streamable HTTP (transport: 'http')."
        )

    # Streamable HTTP shape
    if transport == MCP_HTTP_TRANSPORT or "url" in entry:
        url = (entry.get("url") or "").strip()
        if not url:
            raise ValueError(
                f"{label}.mcpServers['{name}']: HTTP transport requires a non-empty 'url'."
            )
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(
                f"{label}.mcpServers['{name}']: 'url' must start with http:// or https://."
            )
        headers = entry.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValueError(f"{label}.mcpServers['{name}']: 'headers' must be an object.")
        for hk, hv in headers.items():
            if not isinstance(hk, str) or not isinstance(hv, str):
                raise ValueError(
                    f"{label}.mcpServers['{name}']: header keys/values must be strings."
                )
        cleaned = {"transport": MCP_HTTP_TRANSPORT, "url": url}
        if headers:
            cleaned["headers"] = headers
        return cleaned

    # Stdio shape (default)
    command = (entry.get("command") or "").strip()
    if not command:
        raise ValueError(
            f"{label}.mcpServers['{name}']: stdio transport requires a non-empty 'command'."
        )
    args = entry.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError(f"{label}.mcpServers['{name}']: 'args' must be a list of strings.")
    env = entry.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError(f"{label}.mcpServers['{name}']: 'env' must be an object.")
    for ek, ev in env.items():
        if not isinstance(ek, str) or not isinstance(ev, str):
            raise ValueError(
                f"{label}.mcpServers['{name}']: env keys/values must be strings."
            )
    return {"command": command, "args": list(args), "env": dict(env)}


def validate_mcp_configuration(raw, label="mcp_configuration"):
    """
    Validate an MCP configuration object.

    Accepts:
      - dict matching {"mcpServers": {<name>: <entry>, ...}}
      - JSON string of the above

    Returns the cleaned dict {"mcpServers": {...}}. Raises ValueError on any issue.
    Empty/missing input returns {}.
    """
    parsed = _coerce_mcp_dict(raw, label)
    if not parsed:
        return {}

    servers = parsed.get("mcpServers")
    if servers is None:
        raise ValueError(
            f"{label}: top-level key 'mcpServers' is required (object of name → server config)."
        )
    if not isinstance(servers, dict):
        raise ValueError(f"{label}: 'mcpServers' must be a JSON object.")
    if not servers:
        raise ValueError(f"{label}: 'mcpServers' must declare at least one server.")

    cleaned_servers = {}
    for name, entry in servers.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}: mcpServers entry names must be non-empty strings.")
        cleaned_servers[name.strip()] = _validate_mcp_server_entry(name.strip(), entry, label)

    return {"mcpServers": cleaned_servers}


def validate_mcp_secrets(raw, label="mcp_secrets"):
    """
    Validate the project-level MCP secrets dict.

    Accepts a dict {KEY: value} where KEY matches MCP_SECRET_KEY_RE
    (UPPER_SNAKE) and value is a non-empty string. Returns a cleaned dict.
    Empty/missing input returns {}.
    """
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: must be a JSON object of KEY → value strings.")
    cleaned = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"{label}: keys must be strings.")
        key = key.strip()
        if not key:
            continue
        if not MCP_SECRET_KEY_RE.match(key):
            raise ValueError(
                f"{label}: key '{key}' must be UPPER_SNAKE_CASE "
                "(letters, digits, underscores; must start with a letter)."
            )
        if key in cleaned:
            raise ValueError(f"{label}: duplicate key '{key}'.")
        if not isinstance(value, str) or value == "":
            raise ValueError(f"{label}: value for '{key}' must be a non-empty string.")
        cleaned[key] = value
    return cleaned


def validate_mcp_oauth_configs(raw, label="mcp_oauth_configs"):
    """
    Validate the project-level OAuth 2.0 app registration dict.

    Shape: {server_name: {auth_url, token_url, client_id, client_secret, scopes?}}

    - server_name must be a non-empty string (cross-checked against actual
      mcpServers keys in validate_project()).
    - auth_url / token_url must start with https:// (always two separate
      OAuth 2.0 endpoints: auth_url → browser redirect, token_url → server POST).
    - client_id required, non-empty string.
    - client_secret required, non-empty string (SECRET_MASK accepted here;
      the real value is restored by _restore_masked_mcp_oauth_configs() on save).
    - scopes optional, string.

    Returns the cleaned dict. Empty/missing input returns {}.
    """
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: must be a JSON object of server_name → OAuth config.")

    cleaned = {}
    for server_name, cfg in raw.items():
        if not isinstance(server_name, str) or not server_name.strip():
            raise ValueError(f"{label}: server names must be non-empty strings.")
        sname = server_name.strip()

        if not isinstance(cfg, dict):
            raise ValueError(f"{label}['{sname}']: must be a JSON object.")

        for field in MCP_OAUTH_REQUIRED_FIELDS:
            val = (cfg.get(field) or "").strip()
            if not val:
                raise ValueError(
                    f"{label}['{sname}']: '{field}' is required and must be non-empty."
                )

        auth_url = cfg["auth_url"].strip()
        token_url = cfg["token_url"].strip()
        if not auth_url.startswith("https://"):
            raise ValueError(
                f"{label}['{sname}']: 'auth_url' must start with https:// "
                "(the OAuth authorization endpoint where users grant consent)."
            )
        if not token_url.startswith("https://"):
            raise ValueError(
                f"{label}['{sname}']: 'token_url' must start with https:// "
                "(the OAuth token endpoint where the server exchanges the code)."
            )

        scopes = (cfg.get("scopes") or "").strip()

        cleaned[sname] = {
            "auth_url": auth_url,
            "token_url": token_url,
            "client_id": cfg["client_id"].strip(),
            "client_secret": cfg["client_secret"].strip(),
            "scopes": scopes,
        }

    return cleaned


def _iter_mcp_string_values(node):
    """Yield every string scalar in a nested dict/list structure."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _iter_mcp_string_values(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_mcp_string_values(v)


def _extract_mcp_placeholders(servers):
    """Return the set of {KEY} placeholder names referenced anywhere in servers."""
    keys = set()
    for s in _iter_mcp_string_values(servers):
        keys.update(MCP_PLACEHOLDER_RE.findall(s))
    return keys


def validate_agent(data):
    """Validate and clean a single assistant agent dict."""
    if not isinstance(data, dict):
        raise ValueError("Agent must be a JSON object.")

    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Agent 'name' is required.")

    # AutoGen requires agent names to be valid Python identifiers.
    name = sanitize_identifier(name, "Agent name")

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

    mcp_tools, mcp_configuration = _validate_agent_mcp(data, name)

    return {
        "name": name,
        "model": model,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "mcp_tools": mcp_tools,
        "mcp_configuration": mcp_configuration,
    }


def _validate_agent_mcp(data, name):
    """Extract and validate per-agent MCP scope + dedicated configuration."""
    raw_scope = (data.get("mcp_tools") or "none")
    if isinstance(raw_scope, str):
        scope = raw_scope.strip().lower() or "none"
    else:
        scope = "none"
    if scope not in MCP_TOOL_SCOPES:
        raise ValueError(
            f"Agent '{name}': 'mcp_tools' must be one of {', '.join(MCP_TOOL_SCOPES)}."
        )

    raw_cfg = data.get("mcp_configuration")
    if scope == "dedicated":
        cleaned = validate_mcp_configuration(
            raw_cfg, label=f"Agent '{name}'.mcp_configuration"
        )
        if not cleaned:
            raise ValueError(
                f"Agent '{name}': 'mcp_configuration' is required when mcp_tools = 'dedicated'."
            )
        return scope, cleaned

    # none / shared → ignore any submitted config
    return scope, {}


VALID_QUORUM = {"all", "first_win", "team_choice"}


def validate_human_gate(data):
    """Validate and clean the optional human gate configuration."""
    if not isinstance(data, dict):
        return {
            "enabled": False,
            "name": "",
            "quorum": "",
            "remote_users": [],
        }

    enabled = bool(data.get("enabled", False))
    name = (data.get("name") or "").strip()

    if enabled and not name:
        raise ValueError("'human_gate.name' is required when human gate is enabled.")

    if not enabled:
        return {
            "enabled": False,
            "name": "",
            "quorum": "",
            "remote_users": [],
        }

    # Sanitize host/leader name — must be a valid identifier (used as AutoGen participant label)
    name = sanitize_identifier(name, "human_gate.name")

    # Validate remote_users — sanitize name using same rules as validate_agent()
    remote_users = []
    for ru in data.get("remote_users") or []:
        if not isinstance(ru, dict):
            continue
        raw_ru_name = (ru.get("name") or "").strip()
        if not raw_ru_name:
            continue
        remote_users.append({
            "name": sanitize_identifier(raw_ru_name, "Remote user name"),
            "description": (ru.get("description") or "").strip(),
        })

    # Quorum: "na" when no remote users, else validate
    if not remote_users:
        quorum = "na"
    else:
        raw_quorum = (data.get("quorum") or "all").strip()
        quorum = raw_quorum if raw_quorum in VALID_QUORUM else "all"

    return {
        "enabled": enabled,
        "name": name,
        "quorum": quorum,
        "remote_users": remote_users,
    }


def validate_team(data, human_gate_enabled, assistant_count=None):
    """Validate and clean team configuration."""
    if not isinstance(data, dict):
        data = {}

    team_type = (data.get("type") or "round_robin").strip()
    if team_type not in TEAM_TYPES:
        raise ValueError(f"'team.type' must be one of {TEAM_TYPES}.")

    if assistant_count == 1 and team_type == "selector":
        raise ValueError(
            "Single-assistant chat mode does not support Selector team type. "
            "Use Round Robin."
        )

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

    cleaned = {
        "type": team_type,
        "max_iterations": max_iterations,
    }

    if team_type == "selector":
        from .model_catalog import get_agent_model_names
        available_models = get_agent_model_names()

        model = (data.get("model") or "").strip()
        if not model:
            raise ValueError("'team.model' is required for Selector team type.")
        if model not in available_models:
            raise ValueError(
                f"'team.model' must be one of {', '.join(available_models)}."
            )

        system_prompt = (data.get("system_prompt") or "").strip()
        if not system_prompt:
            raise ValueError("'team.system_prompt' is required for Selector team type.")

        raw_temperature = data.get("temperature", 0.0)
        try:
            temperature = float(raw_temperature)
            if not (0.0 <= temperature <= 2.0):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("'team.temperature' must be a number between 0 and 2.")

        allow_repeated_raw = data.get("allow_repeated_speaker", True)
        # Checkbox sends "on" from HTML form, or bool from API
        if isinstance(allow_repeated_raw, str):
            allow_repeated_speaker = allow_repeated_raw.lower() in ("on", "true", "1", "yes")
        else:
            allow_repeated_speaker = bool(allow_repeated_raw)

        cleaned["model"] = model
        cleaned["system_prompt"] = system_prompt
        cleaned["temperature"] = temperature
        cleaned["allow_repeated_speaker"] = allow_repeated_speaker

    return cleaned


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


def validate_export_mapping(data, provider_label="trello"):
    """Validate and clean an export_mapping sub-object."""
    if not isinstance(data, dict):
        data = {}

    system_prompt = (data.get("system_prompt") or "").strip()

    model = (data.get("model") or "").strip()
    valid_models = get_agent_model_names()
    if model and model not in valid_models:
        raise ValueError(
            f"'export_mapping.model' '{model}' is not in the model catalog."
        )

    try:
        temperature = float(data.get("temperature") or 0.0)
    except (TypeError, ValueError):
        temperature = 0.0
    temperature = max(0.0, min(2.0, temperature))

    return {"system_prompt": system_prompt, "model": model, "temperature": temperature}


def validate_jira_type_config(raw_type, type_name, agent_names):
    """Validate a single Jira type sub-config (software/service_desk/business)."""
    if not isinstance(raw_type, dict):
        return {"enabled": False}

    type_enabled = bool(raw_type.get("enabled", False))
    cfg = {"enabled": type_enabled}

    if not type_enabled:
        return cfg

    site_url = (raw_type.get("site_url") or "").strip()
    if not site_url:
        raise ValueError(
            f"'integrations.jira.{type_name}.site_url' is required when {type_name} is enabled."
        )

    email = (raw_type.get("email") or "").strip()
    if not email:
        raise ValueError(
            f"'integrations.jira.{type_name}.email' is required when {type_name} is enabled."
        )

    api_key = (raw_type.get("api_key") or "").strip()
    if not api_key:
        raise ValueError(
            f"'integrations.jira.{type_name}.api_key' is required when {type_name} is enabled."
        )

    # Per-type export_agents — reset to [] if any entry no longer matches current agent names
    # (handles rename/remove of assistant agents without blocking the save)
    raw_ea = raw_type.get("export_agents") or []
    if isinstance(raw_ea, str):
        raw_ea = [raw_ea] if raw_ea else []
    export_agents = [n.strip() for n in raw_ea if isinstance(n, str) and n.strip()]
    lower_names = [n.lower() for n in agent_names]
    if any(ea.lower() not in lower_names for ea in export_agents):
        export_agents = []

    cfg["site_url"] = site_url
    cfg["email"] = email
    cfg["api_key"] = api_key
    cfg["default_project_key"] = (raw_type.get("default_project_key") or "").strip()
    cfg["default_project_name"] = (raw_type.get("default_project_name") or "").strip()
    cfg["export_agents"] = export_agents
    cfg["export_mapping"] = validate_export_mapping(raw_type.get("export_mapping") or {}, provider_label=f"jira.{type_name}")

    return cfg


def validate_jira_integration(raw_jira, agent_names):
    """Validate and clean the full jira integration config."""
    if not isinstance(raw_jira, dict):
        return {"enabled": False}

    jira_enabled = bool(raw_jira.get("enabled", False))
    if not jira_enabled:
        return {"enabled": False}

    jira = {
        "enabled": True,
    }

    any_type_enabled = False
    for type_name in JIRA_TYPES:
        raw_type = raw_jira.get(type_name) or {}
        jira[type_name] = validate_jira_type_config(raw_type, type_name, agent_names)
        if jira[type_name].get("enabled"):
            any_type_enabled = True

    if not any_type_enabled:
        raise ValueError(
            "At least one Jira project type (software, service_desk, business) must be enabled when Jira is enabled."
        )

    return jira


def validate_integrations(data, agent_names):
    """Validate and clean the optional integrations configuration."""
    if not isinstance(data, dict):
        return {
            "enabled": False,
            "trello": {"enabled": False},
            "jira": {"enabled": False},
        }

    enabled = bool(data.get("enabled", False))

    if not enabled:
        return {
            "enabled": False,
            "trello": {"enabled": False},
            "jira": {"enabled": False},
        }

    # --- Trello ---
    raw_trello = data.get("trello") or {}
    trello_enabled = bool(raw_trello.get("enabled", False))
    trello = {"enabled": trello_enabled}

    if trello_enabled:
        app_name = (raw_trello.get("app_name") or "").strip()
        if not app_name:
            raise ValueError("'integrations.trello.app_name' is required when Trello is enabled.")

        api_key = (raw_trello.get("api_key") or "").strip()
        if not api_key:
            raise ValueError("'integrations.trello.api_key' is required when Trello is enabled.")

        # Validate export_agents list — reset to [] if any entry no longer matches current agent names
        # (handles rename/remove of assistant agents without blocking the save)
        raw_ea = raw_trello.get("export_agents") or []
        if isinstance(raw_ea, str):
            raw_ea = [raw_ea] if raw_ea else []
        export_agents = [n.strip() for n in raw_ea if isinstance(n, str) and n.strip()]
        lower_names = [n.lower() for n in agent_names]
        if any(ea.lower() not in lower_names for ea in export_agents):
            export_agents = []

        trello["export_agents"] = export_agents
        trello["app_name"] = app_name
        trello["api_key"] = api_key
        trello["token"] = (raw_trello.get("token") or "").strip()
        _tga = raw_trello.get("token_generated_at") or ""
        if hasattr(_tga, "isoformat"):
            if _tga.tzinfo is None:
                _tga = _tga.replace(tzinfo=_timezone.utc)
            _tga = _tga.isoformat()
        trello["token_generated_at"] = (_tga or "").strip()
        trello["default_workspace_id"] = (raw_trello.get("default_workspace_id") or "").strip()
        trello["default_workspace_name"] = (raw_trello.get("default_workspace_name") or raw_trello.get("default_workspace") or "").strip()
        trello["default_board_id"] = (raw_trello.get("default_board_id") or "").strip()
        trello["default_board_name"] = (raw_trello.get("default_board_name") or "").strip()
        trello["default_list_id"] = (raw_trello.get("default_list_id") or "").strip()
        trello["default_list_name"] = (raw_trello.get("default_list_name") or "").strip()
        trello["export_mapping"] = validate_export_mapping(
            raw_trello.get("export_mapping") or {}
        )

    # --- Jira ---
    raw_jira = data.get("jira") or {}
    jira = validate_jira_integration(raw_jira, agent_names)

    # At least one provider must be enabled
    if not trello_enabled and not jira.get("enabled"):
        raise ValueError(
            "At least one export provider (Trello or Jira) must be enabled when integrations are enabled."
        )

    return {
        "enabled": enabled,
        "trello": trello,
        "jira": jira,
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
    assistant_count = len(agents)
    agent_names = [agent["name"].lower() for agent in agents]
    if len(agent_names) != len(set(agent_names)):
        raise ValueError("Assistant agent names must be unique.")

    human_gate = validate_human_gate(data.get("human_gate") or {})
    if assistant_count == 1 and not human_gate["enabled"]:
        raise ValueError(
            "Single-assistant chat mode requires Human Gate to be enabled."
        )

    team = validate_team(
        data.get("team") or {},
        human_gate["enabled"],
        assistant_count=assistant_count,
    )
    integrations = validate_integrations(
        data.get("integrations") or {},
        [a["name"] for a in agents],
    )

    shared_mcp_tools = validate_mcp_configuration(
        data.get("shared_mcp_tools"), label="shared_mcp_tools"
    )
    if any(a.get("mcp_tools") == "shared" for a in agents) and not shared_mcp_tools:
        raise ValueError(
            "'shared_mcp_tools' must define at least one server in 'mcpServers' "
            "when one or more assistant agents use mcp_tools = 'shared'."
        )

    mcp_secrets = validate_mcp_secrets(data.get("mcp_secrets"))
    # Verify every {KEY} placeholder referenced in shared or per-agent MCP
    # configs has a matching entry in mcp_secrets.
    referenced = set()
    referenced.update(_extract_mcp_placeholders(shared_mcp_tools))
    for a in agents:
        if a.get("mcp_tools") == "dedicated":
            referenced.update(_extract_mcp_placeholders(a.get("mcp_configuration") or {}))
    missing = sorted(k for k in referenced if k not in mcp_secrets)
    if missing:
        raise ValueError(
            "MCP configuration references undefined secret(s): "
            + ", ".join("{" + k + "}" for k in missing)
            + ". Add them under 'MCP Secrets' or remove the placeholder."
        )

    mcp_oauth_configs = validate_mcp_oauth_configs(data.get("mcp_oauth_configs"))
    # Cross-validate: every OAuth server_name must match a real mcpServers key.
    if mcp_oauth_configs:
        known_server_names = set()
        known_server_names.update(
            (shared_mcp_tools.get("mcpServers") or {}).keys()
        )
        for a in agents:
            if a.get("mcp_tools") == "dedicated":
                known_server_names.update(
                    (a.get("mcp_configuration", {}).get("mcpServers") or {}).keys()
                )
        orphaned = sorted(k for k in mcp_oauth_configs if k not in known_server_names)
        if orphaned:
            raise ValueError(
                "mcp_oauth_configs key(s) do not match any configured MCP server: "
                + ", ".join(f"'{k}'" for k in orphaned)
                + ". Each key must match a name under shared_mcp_tools.mcpServers or "
                "a dedicated agent's mcp_configuration.mcpServers."
            )

    cleaned = {
        "project_name": project_name,
        "objective": objective,
        "agents": agents,
        "human_gate": human_gate,
        "integrations": integrations,
        "shared_mcp_tools": shared_mcp_tools,
        "mcp_secrets": mcp_secrets,
        "mcp_oauth_configs": mcp_oauth_configs,
    }

    # In single-assistant chat mode, Team Setup is not a persisted contract.
    # Runtime falls back to Round Robin defaults when team config is absent.
    if assistant_count >= 2:
        cleaned["team"] = team

    return cleaned
