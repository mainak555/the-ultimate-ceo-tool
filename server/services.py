"""
Business logic layer — pure functions operating on dicts.

No request/response objects here. Views call these functions
and translate results into HTTP/HTMX responses.
"""

import hmac
import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId

logger = logging.getLogger(__name__)
from pymongo.errors import DuplicateKeyError

from .db import (
    get_collection,
    ensure_indexes,
    CHAT_SESSIONS_COLLECTION,
    PROJECT_SETTINGS_COLLECTION,
)
from . import attachment_service
from . import util
from .model_catalog import (
    get_agent_model_names,
    default_system_prompt_hint,
    selector_prompt_hint as _get_selector_prompt_hint,
    trello_export_prompt_hint as _get_trello_export_prompt_hint,
    jira_export_prompt_hint as _get_jira_export_prompt_hint,
)
from .schemas import validate_project, validate_chat_session


def _coerce_dt_to_iso(value) -> str:
    """Convert a BSON datetime (or already-string value) to an ISO 8601 string.

    Handles:
    - datetime objects (from PyMongo, aware or naive-UTC)
    - strings (already ISO or legacy bare 'HH:MM' — returned unchanged)
    - None / missing → empty string
    """
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value) if value else ""


def _json_size_bytes(value) -> int:
    """Return UTF-8 byte size of JSON payload with datetime-safe serialization."""
    return len(json.dumps(value, ensure_ascii=True, default=util.json_default).encode("utf-8"))

from core.tracing import traced_function


class ProjectDeletionBlocked(ValueError):
    """Raised when a project cannot be deleted due to dependent records."""


from django.conf import settings as _django_settings

# Read from settings (env var MAX_AGENT_STATE_BYTES, default 1 MB).
# Agent state lives inside the chat_sessions MongoDB document (16 MB hard limit
# shared with discussions[]).  AutoGen serializes the full message history
# including base64 image bytes, so image-heavy sessions can exceed the default;
# raise MAX_AGENT_STATE_BYTES in the environment for those deployments.
MAX_AGENT_STATE_BYTES: int = getattr(_django_settings, "MAX_AGENT_STATE_BYTES", 1_000_000)


def _coerce_temperature(value):
    """Return a float temperature with a safe default for legacy documents."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.7


def get_available_models():
    """Return model names sorted ascending for UI rendering."""
    return get_agent_model_names()


def get_system_prompt_template():
    """Return the default editable system prompt template."""
    return default_system_prompt_hint()


def get_selector_prompt_hint():
    """Return the example selector routing prompt shown as a UI hint."""
    return _get_selector_prompt_hint()


def get_trello_export_prompt_hint():
    """Return the default Trello export system prompt template."""
    return _get_trello_export_prompt_hint()


def get_jira_export_prompt_hint(type_name):
    """Return the default Jira export system prompt for a given project type."""
    return _get_jira_export_prompt_hint(type_name)


SECRET_MASK = "••••••••"
SUPPORTED_EXPORT_PROVIDERS = ("trello", "jira", "pdf", "n8n")


def _mask_secret(value):
    """Return SECRET_MASK if value is non-empty, else empty string."""
    return SECRET_MASK if value else ""


_VALID_MCP_SCOPES = ("none", "shared", "dedicated")


def _normalize_mcp_scope(value):
    """Return one of 'none' | 'shared' | 'dedicated'. Defaults to 'none'."""
    if not isinstance(value, str):
        return "none"
    scope = value.strip().lower()
    return scope if scope in _VALID_MCP_SCOPES else "none"


def _normalize_mcp_dict(value):
    """Return a dict for stored MCP configuration; non-dicts and falsy values become {}."""
    if isinstance(value, dict):
        return value
    return {}


def _mask_mcp_secrets(raw):
    """Return {KEY: SECRET_MASK} for every stored MCP secret key. Non-dict → {}."""
    if not isinstance(raw, dict):
        return {}
    return {k: SECRET_MASK for k in raw.keys() if isinstance(k, str) and k}


def _mask_mcp_oauth_configs(raw):
    """Return a copy of mcp_oauth_configs with client_secret replaced by SECRET_MASK."""
    if not isinstance(raw, dict):
        return {}
    masked = {}
    for server_name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        masked[server_name] = {
            "auth_url": cfg.get("auth_url", ""),
            "token_url": cfg.get("token_url", ""),
            "client_id": cfg.get("client_id", ""),
            "client_secret": SECRET_MASK if cfg.get("client_secret") else "",
            "scopes": cfg.get("scopes", ""),
        }
    return masked


def _restore_masked_mcp_oauth_configs(submitted, existing_configs):
    """
    For each server_name in submitted mcp_oauth_configs, if client_secret is
    SECRET_MASK restore it from existing_configs. Keys absent from submitted
    are treated as user deletions and dropped (not restored).
    """
    if not isinstance(submitted, dict):
        return {}
    existing_configs = existing_configs or {}
    restored = {}
    for server_name, cfg in submitted.items():
        if not isinstance(cfg, dict):
            continue
        existing_entry = existing_configs.get(server_name) or {}
        secret = cfg.get("client_secret", "")
        if secret == SECRET_MASK:
            secret = existing_entry.get("client_secret", "")
        restored[server_name] = {
            "auth_url": cfg.get("auth_url", ""),
            "token_url": cfg.get("token_url", ""),
            "client_id": cfg.get("client_id", ""),
            "client_secret": secret,
            "scopes": cfg.get("scopes", ""),
        }
    return restored


def _normalize_export_agents(raw_trello, raw_integrations):
    """Return a list of export agent names, migrating legacy single-string field."""
    raw_ea = raw_trello.get("export_agents")
    if raw_ea is None:
        # Backward-compat: old documents stored a single string at integrations root
        legacy = (raw_integrations.get("export_agent") or "").strip()
        return [legacy] if legacy else []
    if isinstance(raw_ea, str):
        return [raw_ea.strip()] if raw_ea.strip() else []
    return [n.strip() for n in raw_ea if isinstance(n, str) and n.strip()]


def _normalize_provider_flags(raw_integrations, provider_name):
    """Return enabled/export_agents fields for non-Trello providers."""
    raw_provider = raw_integrations.get(provider_name) or {}
    provider_enabled = bool(raw_provider.get("enabled", False))
    provider = {"enabled": provider_enabled}
    if provider_enabled:
        raw_ea = raw_provider.get("export_agents")
        if isinstance(raw_ea, str):
            raw_ea = [raw_ea] if raw_ea.strip() else []
        provider["export_agents"] = [
            name.strip() for name in (raw_ea or []) if isinstance(name, str) and name.strip()
        ]
    return provider


def normalize_project(project):
    """Normalize stored project documents for display across old and new schemas."""
    if not project:
        return None

    available_models = get_available_models()
    default_model = available_models[0] if available_models else ""
    default_prompt = get_system_prompt_template()

    raw_human_gate = project.get("human_gate") or None
    assistants = []
    for raw_agent in project.get("agents") or []:
        if raw_agent.get("type") == "human_proxy":
            if raw_human_gate is None:
                raw_human_gate = {
                    "enabled": True,
                    "name": raw_agent.get("name") or "Architect",
                }
            continue

        llm_config = raw_agent.get("llm_config") or {}
        assistants.append({
            "name": (raw_agent.get("name") or "").strip(),
            "model": (raw_agent.get("model") or raw_agent.get("model_name") or default_model).strip(),
            "system_prompt": (
                raw_agent.get("system_prompt")
                or raw_agent.get("persona")
                or default_prompt
            ).strip(),
            "temperature": _coerce_temperature(
                raw_agent.get("temperature", llm_config.get("temperature", 0.7))
            ),
            "mcp_tools": _normalize_mcp_scope(raw_agent.get("mcp_tools")),
            "mcp_configuration": _normalize_mcp_dict(raw_agent.get("mcp_configuration")),
        })

    if not assistants:
        assistants = [{
            "name": "",
            "model": default_model,
            "system_prompt": default_prompt,
            "temperature": 0.7,
            "mcp_tools": "none",
            "mcp_configuration": {},
        }]

    human_gate = {
        "enabled": False,
        "name": "",
        "quorum": "",
        "remote_users": [],
    }
    if isinstance(raw_human_gate, dict):
        raw_ru = raw_human_gate.get("remote_users") or []
        remote_users = [
            {
                "name": (ru.get("name") or "").strip(),
                "description": (ru.get("description") or "").strip(),
            }
            for ru in raw_ru
            if isinstance(ru, dict) and (ru.get("name") or "").strip()
        ]
        human_gate = {
            "enabled": bool(raw_human_gate.get("enabled", True)),
            "name": (raw_human_gate.get("name") or "").strip(),
            "quorum": (raw_human_gate.get("quorum") or "").strip(),
            "remote_users": remote_users,
        }

    raw_team = project.get("team") or {}
    team = {
        "type": (raw_team.get("type") or "round_robin").strip() or "round_robin",
        "max_iterations": raw_team.get("max_iterations", project.get("max_iterations", 5)),
        "model": raw_team.get("model", ""),
        "system_prompt": raw_team.get("system_prompt", ""),
        "temperature": _coerce_temperature(raw_team.get("temperature", 0.0)),
        "allow_repeated_speaker": raw_team.get("allow_repeated_speaker", True),
    }

    # --- Integrations ---
    raw_integrations = project.get("integrations") or {}
    integrations_enabled = bool(raw_integrations.get("enabled", False))

    raw_trello = raw_integrations.get("trello") or {}
    trello_enabled = bool(raw_trello.get("enabled", False))
    trello = {"enabled": trello_enabled}
    if trello_enabled:
        trello["export_agents"] = _normalize_export_agents(raw_trello, raw_integrations)
        trello["app_name"] = (raw_trello.get("app_name") or "").strip()
        trello["api_key"] = _mask_secret(raw_trello.get("api_key"))
        trello["token"] = _mask_secret(raw_trello.get("token"))
        trello["token_generated_at"] = _coerce_dt_to_iso(raw_trello.get("token_generated_at") or "")
        trello["default_workspace_id"] = (raw_trello.get("default_workspace_id") or "").strip()
        trello["default_workspace_name"] = (raw_trello.get("default_workspace_name") or raw_trello.get("default_workspace") or "").strip()
        trello["default_board_id"] = (raw_trello.get("default_board_id") or "").strip()
        trello["default_board_name"] = (raw_trello.get("default_board_name") or "").strip()
        trello["default_list_id"] = (raw_trello.get("default_list_id") or "").strip()
        trello["default_list_name"] = (raw_trello.get("default_list_name") or "").strip()
        raw_trello_mapping = raw_trello.get("export_mapping") or {}
        trello["export_mapping"] = {
            "system_prompt": (raw_trello_mapping.get("system_prompt") or "").strip(),
            "model": (raw_trello_mapping.get("model") or "").strip(),
            "temperature": _coerce_temperature(raw_trello_mapping.get("temperature", 0.0)),
        }

    integrations = {
        "enabled": integrations_enabled,
        "trello": trello,
    }

    # --- Jira ---
    raw_jira = raw_integrations.get("jira") or {}
    jira_enabled = bool(raw_jira.get("enabled", False))
    jira = {"enabled": jira_enabled}
    if jira_enabled:
        for jira_type in ("software", "service_desk", "business"):
            raw_type = raw_jira.get(jira_type) or {}
            type_enabled = bool(raw_type.get("enabled", False))
            type_cfg = {"enabled": type_enabled}
            if type_enabled:
                type_cfg["site_url"] = (raw_type.get("site_url") or "").strip()
                type_cfg["email"] = (raw_type.get("email") or "").strip()
                type_cfg["api_key"] = _mask_secret(raw_type.get("api_key"))
                type_cfg["default_project_key"] = (raw_type.get("default_project_key") or "").strip()
                type_cfg["default_project_name"] = (raw_type.get("default_project_name") or "").strip()
                type_cfg["export_agents"] = _normalize_export_agents(raw_type, {})
                raw_mapping = raw_type.get("export_mapping") or {}
                type_cfg["export_mapping"] = {
                    "system_prompt": (raw_mapping.get("system_prompt") or "").strip(),
                    "model": (raw_mapping.get("model") or "").strip(),
                    "temperature": _coerce_temperature(raw_mapping.get("temperature", 0.0)),
                }
            jira[jira_type] = type_cfg
    integrations["jira"] = jira

    for provider_name in SUPPORTED_EXPORT_PROVIDERS:
        if provider_name in ("trello", "jira"):
            continue
        integrations[provider_name] = _normalize_provider_flags(raw_integrations, provider_name)

    return {
        "project_id": str(project["_id"]) if project.get("_id") else "",
        "project_name": project.get("project_name", ""),
        "objective": project.get("objective", ""),
        "version": round(float(project.get("version") or 1.0), 1),
        "created_at": _coerce_dt_to_iso(project.get("created_at")),
        "updated_at": _coerce_dt_to_iso(project.get("updated_at")),
        "agents": assistants,
        "human_gate": human_gate,
        "team": team,
        "integrations": integrations,
        "shared_mcp_tools": _normalize_mcp_dict(project.get("shared_mcp_tools")),
        "mcp_secrets": _mask_mcp_secrets(project.get("mcp_secrets")),
        "mcp_oauth_configs": _mask_mcp_oauth_configs(project.get("mcp_oauth_configs")),
        "has_chat_sessions": False,
    }


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

def list_projects():
    """Return all project settings, sorted by project_name ascending."""
    ensure_indexes()
    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    cursor = col.find({}).sort("project_name", 1)
    projects = [normalize_project(p) for p in cursor]
    project_ids = [p["project_id"] for p in projects if p.get("project_id")]
    if not project_ids:
        return projects

    sessions_col = get_collection(CHAT_SESSIONS_COLLECTION)
    chat_project_ids = set(
        sessions_col.distinct("project_id", {"project_id": {"$in": project_ids}})
    )
    for project in projects:
        project["has_chat_sessions"] = project.get("project_id") in chat_project_ids

    return projects


def get_project(project_id):
    """Return a single project by _id (hex string), or None if not found."""
    ensure_indexes()
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        return None
    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    project = col.find_one({"_id": oid})
    normalized = normalize_project(project)
    if not normalized:
        return None
    normalized["has_chat_sessions"] = _project_has_chat_sessions(project_id)
    return normalized


def _project_has_chat_sessions(project_id):
    """Return True when at least one chat session references project_id."""
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    return col.find_one({"project_id": project_id}, {"_id": 1}) is not None


def get_project_raw(project_id):
    """Return raw (unmasked) project doc for export pipeline use. None if not found."""
    ensure_indexes()
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        return None
    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    doc = col.find_one({"_id": oid})
    if doc:
        doc["project_id"] = str(doc.pop("_id"))
    return doc


@traced_function("service.project.create")
def create_project(data, initial_version=1.0):
    """
    Validate and insert a new project configuration.

    initial_version controls the starting version float (default 1.0; clones pass a
    major-bumped value, e.g. 2.0 when cloning a 1.x project).

    Returns the created document (with project_id populated).
    Raises ValueError on validation errors or duplicate name.
    """
    cleaned = validate_project(data)

    ensure_indexes()
    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    doc = cleaned.copy()
    now = util.utc_now()
    doc["created_at"] = now
    doc["updated_at"] = now
    doc["version"] = round(float(initial_version), 1)
    try:
        col.insert_one(doc)
    except DuplicateKeyError:
        logger.warning(
            "project.create_duplicate",
            extra={"project_name": cleaned.get("project_name", "")},
        )
        raise ValueError(
            f"A project named '{cleaned['project_name']}' already exists."
        )

    normalized = normalize_project(doc)
    logger.info(
        "project.created",
        extra={
            "project_id": str(normalized.get("project_id", "")),
            "project_name": normalized.get("project_name", ""),
        },
    )
    return normalized


@traced_function("service.project.update")
def update_project(project_id, data):
    """
    Validate and update an existing project configuration.

    project_id is the MongoDB _id hex string. Project name may be changed.
    Returns the updated document.
    Raises ValueError on validation errors or if the project doesn't exist.
    """
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid project ID '{project_id}'.")

    # Load existing doc to preserve masked secrets
    ensure_indexes()
    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    existing = col.find_one({"_id": oid})
    if existing is None:
        raise ValueError("Project not found.")

    # Before validation, replace masked secrets with originals from DB
    _restore_masked_secrets(data, existing)

    cleaned = validate_project(data)

    # Preserve original created_at; stamp updated_at as BSON Date
    cleaned["created_at"] = existing.get("created_at") or util.utc_now()
    cleaned["updated_at"] = util.utc_now()
    # Increment the decimal part of the version on every save (1.0 → 1.1 → 1.2 …)
    current_version = float(existing.get("version") or 1.0)
    cleaned["version"] = round(current_version + 0.1, 1)

    try:
        result = col.replace_one({"_id": oid}, cleaned)
    except DuplicateKeyError:
        logger.warning(
            "project.update_duplicate",
            extra={"project_id": project_id, "project_name": cleaned.get("project_name", "")},
        )
        raise ValueError(
            f"A project named '{cleaned['project_name']}' already exists."
        )
    if result.matched_count == 0:
        raise ValueError(f"Project not found.")

    cleaned["_id"] = oid
    normalized = normalize_project(cleaned)
    logger.info(
        "project.updated",
        extra={"project_id": project_id, "project_name": normalized.get("project_name", "")},
    )
    return normalized


def _restore_masked_secrets(data, existing):
    """Replace SECRET_MASK placeholders in data with actual values from the DB."""
    integrations = data.get("integrations")
    if isinstance(integrations, dict):
        existing_integrations = existing.get("integrations") or {}

        # Trello secrets
        trello = integrations.get("trello")
        existing_trello = existing_integrations.get("trello") or {}
        if isinstance(trello, dict):
            if trello.get("api_key") == SECRET_MASK:
                trello["api_key"] = existing_trello.get("api_key", "")
            token_value = trello.get("token", "")
            if token_value == SECRET_MASK or (not token_value and existing_trello.get("token")):
                trello["token"] = existing_trello.get("token", "")
            # Preserve token_generated_at from DB when not explicitly set
            if not trello.get("token_generated_at"):
                trello["token_generated_at"] = existing_trello.get("token_generated_at", "")

        # Jira secrets (per type)
        jira = integrations.get("jira")
        existing_jira = existing_integrations.get("jira") or {}
        if isinstance(jira, dict):
            for jira_type in ("software", "service_desk", "business"):
                type_cfg = jira.get(jira_type)
                existing_type = existing_jira.get(jira_type) or {}
                if isinstance(type_cfg, dict):
                    if type_cfg.get("api_key") == SECRET_MASK:
                        type_cfg["api_key"] = existing_type.get("api_key", "")

    # MCP secrets — restore masked values from existing project doc.
    # Submitted dict is authoritative for which keys exist (deletions persist).
    mcp_secrets = data.get("mcp_secrets")
    if isinstance(mcp_secrets, dict):
        existing_secrets = existing.get("mcp_secrets") or {}
        for key, value in list(mcp_secrets.items()):
            if value == SECRET_MASK:
                if key in existing_secrets:
                    mcp_secrets[key] = existing_secrets[key]
                else:
                    # Mask submitted with no prior value → drop (validation
                    # would otherwise reject empty value).
                    mcp_secrets.pop(key, None)

    # MCP OAuth configs — restore masked client_secrets per server_name.
    mcp_oauth_submitted = data.get("mcp_oauth_configs")
    if isinstance(mcp_oauth_submitted, dict):
        data["mcp_oauth_configs"] = _restore_masked_mcp_oauth_configs(
            mcp_oauth_submitted, existing.get("mcp_oauth_configs") or {}
        )


@traced_function("service.project.delete")
def delete_project(project_id):
    """Delete a project by _id (hex string). Raises ValueError if not found."""
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid project ID '{project_id}'.")

    ensure_indexes()
    if _project_has_chat_sessions(project_id):
        raise ProjectDeletionBlocked(
            "Cannot delete project while chat sessions exist. Delete chat sessions first."
        )

    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    result = col.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise ValueError("Project not found.")
    logger.info("project.deleted", extra={"project_id": project_id})


@traced_function("service.project.clone")
def clone_project(project_id):
    """
    Clone an existing project as '{name} - Copy'.

    Returns the newly created project document.
    Raises ValueError if the source project is not found or the cloned name is a duplicate.
    """
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError("Project not found.")

    ensure_indexes()
    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    raw = col.find_one({"_id": oid})
    if raw is None:
        raise ValueError("Project not found.")

    source = normalize_project(raw)

    # Build clone data with raw (unmasked) secrets
    raw_integrations = raw.get("integrations") or {}
    data = {
        "project_name": f"{source['project_name']} - Copy",
        "objective": source["objective"],
        "agents": source["agents"],
        "human_gate": source["human_gate"],
        "team": source["team"],
        "integrations": raw_integrations,
        "shared_mcp_tools": raw.get("shared_mcp_tools") or {},
        "mcp_secrets": raw.get("mcp_secrets") or {},
    }
    # Bump the major version: 1.x → 2.0, 2.x → 3.0, etc.
    source_version = float(raw.get("version") or 1.0)
    clone_version = float(int(source_version) + 1)
    return create_project(data, initial_version=clone_version)


# ---------------------------------------------------------------------------
# Chat Session CRUD
# ---------------------------------------------------------------------------

def _normalize_discussion(msg):
    """Return a copy of a discussion dict with timestamp coerced to ISO string.

    Handles both new BSON Date values (datetime objects returned by PyMongo)
    and old bare "HH:MM" string values already stored in MongoDB.
    """
    if not isinstance(msg, dict):
        return msg
    ts = msg.get("timestamp", "")
    if hasattr(ts, "isoformat"):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.isoformat()
    row = dict(msg, timestamp=ts)
    attachments = []
    for item in row.get("attachments", []) or []:
        if not isinstance(item, dict):
            continue
        att = dict(item)
        uploaded_at = att.get("uploaded_at")
        if hasattr(uploaded_at, "isoformat"):
            if uploaded_at.tzinfo is None:
                uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
            att["uploaded_at"] = uploaded_at.isoformat()
        attachments.append(att)
    if attachments:
        row["attachments"] = attachments
    # Coerce export payload datetimes (updated_at, last_push.pushed_at) to ISO strings.
    exports = row.get("exports")
    if isinstance(exports, dict):
        coerced_exports = {}
        for provider_key, provider_val in exports.items():
            if not isinstance(provider_val, dict):
                coerced_exports[provider_key] = provider_val
                continue
            # Handles both flat (trello) and nested (jira.software/service_desk/business) shapes.
            if any(isinstance(v, dict) for v in provider_val.values()):
                # Nested shape: e.g. jira -> {software: {...}, service_desk: {...}}
                coerced_provider = {}
                for sub_key, sub_val in provider_val.items():
                    if isinstance(sub_val, dict):
                        coerced_provider[sub_key] = _coerce_export_payload_dates(sub_val)
                    else:
                        coerced_provider[sub_key] = sub_val
                coerced_exports[provider_key] = coerced_provider
            else:
                coerced_exports[provider_key] = _coerce_export_payload_dates(provider_val)
        row["exports"] = coerced_exports
    return row


def _coerce_export_payload_dates(payload):
    """Return a shallow copy of an export payload dict with datetime fields coerced to ISO strings."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    out["updated_at"] = _coerce_dt_to_iso(out.get("updated_at"))
    last_push = out.get("last_push")
    if isinstance(last_push, dict):
        lp = dict(last_push)
        lp["pushed_at"] = _coerce_dt_to_iso(lp.get("pushed_at"))
        out["last_push"] = lp
    return out


def normalize_chat_session(doc):
    """Convert a MongoDB chat_sessions document for display."""
    if not doc:
        return None
    created_at = doc.get("created_at", "")
    if hasattr(created_at, "strftime"):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at = created_at.isoformat()
    agent_state = doc.get("agent_state")
    state_meta = {}
    if isinstance(agent_state, dict):
        state_meta = {
            "source": agent_state.get("source", ""),
            "version": agent_state.get("version", ""),
            "saved_at": _coerce_dt_to_iso(agent_state.get("saved_at", "")),
        }
    pending_oauth = doc.get("pending_oauth_servers") or []
    if not isinstance(pending_oauth, list):
        pending_oauth = []
    return {
        "session_id": str(doc["_id"]),
        "project_id": doc.get("project_id", ""),
        "description": doc.get("description", ""),
        "created_at": created_at,
        "discussions": [_normalize_discussion(m) for m in doc.get("discussions", [])],
        "status": doc.get("status", "idle"),
        "current_round": doc.get("current_round", 0),
        "has_agent_state": isinstance(agent_state, dict) and isinstance(agent_state.get("state"), dict),
        "agent_state_meta": state_meta,
        "pending_oauth_servers": [str(s) for s in pending_oauth if isinstance(s, str)],
    }


def _ensure_discussion_ids(doc, col=None):
    """Ensure each discussions entry has a stable UUID id."""
    if not isinstance(doc, dict):
        return doc

    discussions = doc.get("discussions")
    if not isinstance(discussions, list):
        return doc

    changed = False
    for item in discussions:
        if not isinstance(item, dict):
            continue
        message_id = item.get("id")
        if isinstance(message_id, str) and message_id.strip():
            continue
        item["id"] = str(uuid4())
        changed = True

    if changed and col is not None and doc.get("_id"):
        col.update_one({"_id": doc["_id"]}, {"$set": {"discussions": discussions}})

    return doc


@traced_function("service.chat.create")
def create_chat_session(project_id, description):
    """Insert a new chat session. Returns the normalized document."""
    cleaned = validate_chat_session({"project_id": project_id, "description": description})
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    doc = {
        "project_id": cleaned["project_id"],
        "description": cleaned["description"],
        "created_at": datetime.now(timezone.utc),
        "discussions": [],
        "status": "idle",
        "current_round": 0,
    }
    col.insert_one(doc)
    normalized = normalize_chat_session(doc)
    logger.info(
        "chat.session.created",
        extra={
            "session_id": str(normalized.get("session_id", "")),
            "project_id": cleaned["project_id"],
        },
    )
    return normalized


def set_session_status(session_id, status):
    """Set status (and optionally increment round) on a chat session."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    update = {"$set": {"status": status}}
    if status == "awaiting_input":
        update["$inc"] = {"current_round": 1}
    col.update_one({"_id": oid}, update)


@traced_function("service.chat.try_set_running")
def try_set_session_running(session_id):
    """Atomically set status to running only from idle/awaiting_input/awaiting_mcp_oauth."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return False

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    result = col.update_one(
        {"_id": oid, "status": {"$in": ["idle", "awaiting_input", "awaiting_mcp_oauth"]}},
        {"$set": {"status": "running"}},
    )
    return result.modified_count == 1


@traced_function("service.chat.set_awaiting_mcp_oauth")
def set_session_awaiting_oauth(session_id, server_names=None):
    """Mark a chat session as awaiting MCP OAuth authorization.

    Flips ``status`` to ``awaiting_mcp_oauth``.  Idempotent.
    ``server_names`` is accepted for backward compatibility but not stored —
    the pending list is tracked entirely via Redis (readiness counter + pub/sub).
    """
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    col.update_one(
        {"_id": oid},
        {"$set": {"status": "awaiting_mcp_oauth"}},
    )


def compute_pending_oauth_servers(raw_project, session_id):
    """Return the list of MCP server names that are reachable by this run,
    require OAuth, and have no session-scoped Bearer token in Redis.

    ``raw_project`` must be the unmasked project dict (from ``get_project_raw``)
    so that ``mcp_oauth_configs`` and ``mcpServers`` keys are visible.
    """
    if not isinstance(raw_project, dict) or not session_id:
        return []
    oauth_configs = raw_project.get("mcp_oauth_configs") or {}
    if not isinstance(oauth_configs, dict) or not oauth_configs:
        return []

    reachable: set[str] = set()
    agents = raw_project.get("agents") or []
    has_shared = False
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        scope = (agent.get("mcp_tools") or "none").strip().lower()
        if scope == "shared":
            has_shared = True
        elif scope == "dedicated":
            cfg = agent.get("mcp_configuration") or {}
            servers = (cfg or {}).get("mcpServers") or {}
            if isinstance(servers, dict):
                reachable.update(servers.keys())
    if has_shared:
        shared_cfg = raw_project.get("shared_mcp_tools") or {}
        shared_servers = (shared_cfg or {}).get("mcpServers") or {}
        if isinstance(shared_servers, dict):
            reachable.update(shared_servers.keys())

    needs_auth = sorted(reachable.intersection(oauth_configs.keys()))
    if not needs_auth:
        return []

    from agents.session_coordination import list_authorized_oauth_servers
    authorized = set(list_authorized_oauth_servers(session_id, needs_auth))
    return [name for name in needs_auth if name not in authorized]


def list_all_reachable_oauth_servers(raw_project):
    """Return all MCP server names that are reachable by this project AND require OAuth.

    Unlike ``compute_pending_oauth_servers`` this does NOT check Redis for
    existing tokens — it returns the full set regardless of authorization state.
    Used to compute ``total_count`` for the readiness counter.
    """
    if not isinstance(raw_project, dict):
        return []
    oauth_configs = raw_project.get("mcp_oauth_configs") or {}
    if not isinstance(oauth_configs, dict) or not oauth_configs:
        return []

    reachable: set[str] = set()
    agents = raw_project.get("agents") or []
    has_shared = False
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        scope = (agent.get("mcp_tools") or "none").strip().lower()
        if scope == "shared":
            has_shared = True
        elif scope == "dedicated":
            cfg = agent.get("mcp_configuration") or {}
            servers = (cfg or {}).get("mcpServers") or {}
            if isinstance(servers, dict):
                reachable.update(servers.keys())
    if has_shared:
        shared_cfg = raw_project.get("shared_mcp_tools") or {}
        shared_servers = (shared_cfg or {}).get("mcpServers") or {}
        if isinstance(shared_servers, dict):
            reachable.update(shared_servers.keys())

    return sorted(reachable.intersection(oauth_configs.keys()))


@traced_function("service.chat.append_messages")
def append_messages(session_id, messages):
    """Append a list of message dicts to session discussions."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return
    to_append = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        row = dict(msg)
        message_id = row.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            row["id"] = str(uuid4())
        to_append.append(row)

    if not to_append:
        return

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    col.update_one({"_id": oid}, {"$push": {"discussions": {"$each": to_append}}})


def get_discussion_export_payload(session_id, discussion_id, provider, subkey=None):
    """Return saved export payload for a discussion/provider (optional subkey), or None."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    target_discussion_id = (discussion_id or "").strip()
    if not target_discussion_id:
        raise ValueError("'discussion_id' is required.")

    provider_name = (provider or "").strip().lower()
    if not provider_name:
        raise ValueError("'provider' is required.")

    provider_subkey = None
    if subkey is not None:
        provider_subkey = (subkey or "").strip().lower()
        if not provider_subkey:
            raise ValueError("'subkey' must be non-empty when provided.")

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    session_doc = col.find_one({"_id": oid}, {"discussions": 1})
    if not session_doc:
        raise ValueError("Chat session not found.")

    for row in session_doc.get("discussions") or []:
        if not isinstance(row, dict):
            continue
        if (row.get("id") or "").strip() != target_discussion_id:
            continue
        exports = row.get("exports")
        if not isinstance(exports, dict):
            return None
        payload = exports.get(provider_name)
        if provider_subkey is not None:
            if not isinstance(payload, dict):
                return None
            payload = payload.get(provider_subkey)
        return payload if isinstance(payload, dict) else None

    raise ValueError("Discussion item not found for this session.")


@traced_function("service.discussion.set_export_payload")
def set_discussion_export_payload(session_id, discussion_id, provider, payload, subkey=None):
    """Persist export payload for a discussion/provider (optional subkey) and return saved payload."""
    if not isinstance(payload, dict):
        raise ValueError("'payload' must be a JSON object.")

    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    target_discussion_id = (discussion_id or "").strip()
    if not target_discussion_id:
        raise ValueError("'discussion_id' is required.")

    provider_name = (provider or "").strip().lower()
    if not provider_name:
        raise ValueError("'provider' is required.")

    provider_subkey = None
    if subkey is not None:
        provider_subkey = (subkey or "").strip().lower()
        if not provider_subkey:
            raise ValueError("'subkey' must be non-empty when provided.")

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    session_doc = col.find_one({"_id": oid})
    if not session_doc:
        raise ValueError("Chat session not found.")

    discussions = session_doc.get("discussions")
    if not isinstance(discussions, list):
        raise ValueError("Discussion list is missing on session.")

    found = False
    for row in discussions:
        if not isinstance(row, dict):
            continue
        if (row.get("id") or "").strip() != target_discussion_id:
            continue
        exports = row.get("exports")
        if not isinstance(exports, dict):
            exports = {}
        if provider_subkey is None:
            exports[provider_name] = payload
        else:
            provider_obj = exports.get(provider_name)
            if not isinstance(provider_obj, dict):
                provider_obj = {}
            provider_obj[provider_subkey] = payload
            exports[provider_name] = provider_obj
        row["exports"] = exports
        found = True
        break

    if not found:
        raise ValueError("Discussion item not found for this session.")

    col.update_one({"_id": oid}, {"$set": {"discussions": discussions}})
    return payload


@traced_function("service.chat.save_agent_state")
def save_agent_state(session_id, state):
    """Persist serialized AutoGen TeamState for a chat session."""
    if not isinstance(state, dict):
        raise ValueError("'state' must be a JSON object.")

    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    payload = {
        "source": "autogen_team_state",
        "version": str(state.get("version") or ""),
        "saved_at": util.utc_now(),  # BSON Date — coerced to ISO string on read
        "state": state,
    }
    try:
        payload_size = _json_size_bytes(payload)
    except TypeError as e:
        raise TypeError(f"Serialization failed: {e}")
    except ValueError as e:
        raise TypeError(f"JSON format error: {e}")
    
    if payload_size > MAX_AGENT_STATE_BYTES:
        raise ValueError(
            f"Agent state is too large to persist "
            f"({payload_size:,} bytes; limit {MAX_AGENT_STATE_BYTES:,} bytes)."
        )

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    result = col.update_one({"_id": oid}, {"$set": {"agent_state": payload}})
    if result.matched_count == 0:
        raise ValueError("Chat session not found.")


def get_agent_state(session_id):
    """Return persisted AutoGen TeamState dict for a session, or None."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return None

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    raw = col.find_one({"_id": oid}, {"agent_state": 1})
    if not raw:
        return None
    agent_state = raw.get("agent_state")
    if not isinstance(agent_state, dict):
        return None
    state = agent_state.get("state")
    return state if isinstance(state, dict) else None


def clear_agent_state(session_id):
    """Remove persisted AutoGen TeamState for a session."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    col.update_one({"_id": oid}, {"$unset": {"agent_state": ""}})


def list_chat_sessions(project_id):
    """Return all sessions for a project, sorted newest first."""
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    cursor = col.find({"project_id": project_id}).sort("created_at", -1)
    docs = []
    for doc in cursor:
        docs.append(_ensure_discussion_ids(doc, col=col))
    return [normalize_chat_session(d) for d in docs]


def get_chat_session(session_id):
    """Return a single chat session by _id hex string, or None."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return None
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    doc = col.find_one({"_id": oid})
    doc = _ensure_discussion_ids(doc, col=col)
    return normalize_chat_session(doc)


@traced_function("service.chat.delete")
def delete_chat_session(session_id):
    """Delete a chat session by _id hex string. Raises ValueError if not found."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")
    attachment_service.delete_session_attachments(session_id)
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    result = col.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise ValueError("Chat session not found.")


def update_chat_session(session_id, description):
    """Update the description of a chat session. Returns the normalized doc."""
    description = (description or "").strip()
    if not description:
        raise ValueError("'description' is required.")
    if len(description) > 150:
        description = description[:150]
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    result = col.update_one({"_id": oid}, {"$set": {"description": description}})
    if result.matched_count == 0:
        raise ValueError("Chat session not found.")
    return normalize_chat_session(col.find_one({"_id": oid}))


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def verify_secret_key(key):
    """
    Constant-time comparison of the provided key against APP_SECRET_KEY.

    Returns True if the key matches, False otherwise.
    """
    expected = os.getenv("APP_SECRET_KEY", "")
    if not expected:
        return False
    return hmac.compare_digest(key, expected)
