"""Shared server utility helpers.

This module hosts small, reusable, side-effect-light helpers used across
feature views/services to keep behavior consistent and avoid helper duplication.
"""

from datetime import datetime, timezone
import json
import re

from django.http import HttpResponse


# ---------------------------------------------------------------------------
# Quorum options — single source of truth shared by config_form and home gate
# ---------------------------------------------------------------------------
# Order and labels must match config_form.html and the home.js gate panel.
QUORUM_OPTIONS = [
    {"value": "all",         "label": "Wait for all remote users to reply"},
    {"value": "first_win",   "label": "First user response continues the run"},
    {"value": "team_choice", "label": "Let the agent planner decide who must reply"},
]

VALID_QUORUM_VALUES = {opt["value"] for opt in QUORUM_OPTIONS}


def sanitize_identifier(raw_name: str, label: str) -> str:
    """Sanitize a raw string to a valid Python identifier.

    Rules (identical to AutoGen agent name requirements):
    - Spaces and hyphens are replaced with underscores.
    - All remaining non-word characters are stripped.
    - A leading digit triggers a prepended underscore.
    - Raises ValueError if the result is not a valid identifier.
    """
    name = (raw_name or "").strip()
    sanitised = re.sub(r"[\s\-]+", "_", name)
    sanitised = re.sub(r"[^\w]", "", sanitised)
    if sanitised and sanitised[0].isdigit():
        sanitised = "_" + sanitised
    if not sanitised or not sanitised.isidentifier():
        raise ValueError(
            f"{label} '{name}' is not a valid identifier. "
            "Use only letters, digits, and underscores (no spaces or special characters)."
        )
    return sanitised


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime for BSON Date writes."""
    return datetime.now(timezone.utc)


def coerce_confidence(value):
    """Coerce input into a confidence score clamped to the range [0.0, 1.0]."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, out))


def normalize_labels(labels):
    """Return cleaned labels with case-insensitive dedupe while preserving order."""
    if not isinstance(labels, list):
        return []
    seen = set()
    out = []
    for lbl in labels:
        txt = str(lbl or "").strip()
        if txt and txt.lower() not in seen:
            seen.add(txt.lower())
            out.append(txt)
    return out


def json_response(data, status=200):
    """Build a JSON HttpResponse using shared datetime-aware serialization."""
    return HttpResponse(json_dumps(data), status=status, content_type="application/json")


def json_error(message, status=400):
    """Return a standard JSON error payload using the shared response helper."""
    return json_response({"error": message}, status=status)


def json_default(value):
    """Serialize datetime values for JSON boundaries.

    Naive datetimes are treated as UTC to preserve existing DB semantics.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(payload) -> str:
    """Serialize payloads to JSON with the module's shared default encoder."""
    return json.dumps(payload, default=json_default)


# ---------------------------------------------------------------------------
# Export provider registry — single source of truth for supported providers
# ---------------------------------------------------------------------------

SUPPORTED_EXPORT_PROVIDERS = ("trello", "jira", "pdf", "n8n")

EXPORT_PROVIDER_LABELS: dict[str, str] = {
    "trello":            "Trello",
    "jira":              "Jira",
    "jira_software":     "Jira Software",
    "jira_service_desk": "Jira Service Desk",
    "jira_business":     "Jira Business",
    "pdf":               "PDF",
    "n8n":               "n8n",
}


def normalize_export_agents(raw_agents) -> list[str]:
    """Return a clean list of export agent names."""
    if isinstance(raw_agents, str):
        raw_agents = [raw_agents] if raw_agents else []
    if not isinstance(raw_agents, list):
        return []
    return [name.strip() for name in raw_agents if isinstance(name, str) and name.strip()]


def build_export_meta(project: dict) -> dict | None:
    """Build provider metadata for export actions from project integrations.

    Returns a dict ``{"enabled": True, "providers": [...]}`` or ``None`` when
    integrations are disabled or no providers are configured.
    """
    integrations = project.get("integrations") if isinstance(project, dict) else {}
    if not isinstance(integrations, dict) or not integrations.get("enabled", False):
        return None

    providers: list[dict] = []
    for provider_name in SUPPORTED_EXPORT_PROVIDERS:
        provider_cfg = integrations.get(provider_name)
        if not isinstance(provider_cfg, dict) or not provider_cfg.get("enabled", False):
            continue

        if provider_name == "jira":
            # Emit one entry per enabled Jira sub-type, each with its own export_agents
            for jira_type in ("software", "service_desk", "business"):
                type_cfg = provider_cfg.get(jira_type) or {}
                if not type_cfg.get("enabled", False):
                    continue
                sub_key = f"jira_{jira_type}"
                providers.append({
                    "name": sub_key,
                    "label": EXPORT_PROVIDER_LABELS.get(sub_key, sub_key.replace("_", " ").title()),
                    "export_agents": normalize_export_agents(type_cfg.get("export_agents")),
                })
            continue

        providers.append({
            "name": provider_name,
            "label": EXPORT_PROVIDER_LABELS.get(provider_name, provider_name.title()),
            "export_agents": normalize_export_agents(provider_cfg.get("export_agents")),
        })

    if not providers:
        return None

    return {"enabled": True, "providers": providers}


def filter_export_providers(export_meta: dict | None, agent_name: str) -> list[dict]:
    """Return export providers visible for a given agent name.

    An empty ``export_agents`` allowlist means all agents may export.
    """
    if not export_meta or not export_meta.get("enabled"):
        return []

    target = (agent_name or "").strip().lower()
    visible: list[dict] = []
    for provider in export_meta.get("providers") or []:
        allowlist = provider.get("export_agents") or []
        if not allowlist:
            visible.append(provider)
            continue
        if any((name or "").strip().lower() == target for name in allowlist):
            visible.append(provider)
    return visible

