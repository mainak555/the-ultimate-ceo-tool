"""
Business logic layer — pure functions operating on dicts.

No request/response objects here. Views call these functions
and translate results into HTTP/HTMX responses.
"""

import hmac
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from .db import get_collection, ensure_indexes, CHAT_SESSIONS_COLLECTION
from .model_catalog import (
    get_agent_model_names,
    default_system_prompt_hint,
    selector_prompt_hint as _get_selector_prompt_hint,
    trello_export_prompt_hint as _get_trello_export_prompt_hint,
)
from .schemas import validate_project, validate_chat_session


class ProjectDeletionBlocked(ValueError):
    """Raised when a project cannot be deleted due to dependent records."""


MAX_AGENT_STATE_BYTES = 900_000


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


SECRET_MASK = "••••••••"
SUPPORTED_EXPORT_PROVIDERS = ("trello", "jira", "pdf", "n8n")


def _mask_secret(value):
    """Return SECRET_MASK if value is non-empty, else empty string."""
    return SECRET_MASK if value else ""


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
                    "interaction_mode": "feedback"
                    if (raw_agent.get("interaction_mode") or "").strip() == "feedback"
                    else "approve_reject",
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
        })

    if not assistants:
        assistants = [{
            "name": "",
            "model": default_model,
            "system_prompt": default_prompt,
            "temperature": 0.7,
        }]

    human_gate = {
        "enabled": False,
        "name": "",
        "interaction_mode": "approve_reject",
    }
    if isinstance(raw_human_gate, dict):
        human_gate = {
            "enabled": bool(raw_human_gate.get("enabled", True)),
            "name": (raw_human_gate.get("name") or "").strip(),
            "interaction_mode": (
                raw_human_gate.get("interaction_mode") or "approve_reject"
            ).strip() or "approve_reject",
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
        trello["token_generated_at"] = (raw_trello.get("token_generated_at") or "").strip()
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

    for provider_name in SUPPORTED_EXPORT_PROVIDERS:
        if provider_name == "trello":
            continue
        integrations[provider_name] = _normalize_provider_flags(raw_integrations, provider_name)

    return {
        "project_id": str(project["_id"]) if project.get("_id") else "",
        "project_name": project.get("project_name", ""),
        "objective": project.get("objective", ""),
        "agents": assistants,
        "human_gate": human_gate,
        "team": team,
        "integrations": integrations,
        "has_chat_sessions": False,
    }


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

def list_projects():
    """Return all project settings, sorted by project_name ascending."""
    ensure_indexes()
    col = get_collection("project_settings")
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
    col = get_collection("project_settings")
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
    col = get_collection("project_settings")
    doc = col.find_one({"_id": oid})
    if doc:
        doc["project_id"] = str(doc.pop("_id"))
    return doc


def create_project(data):
    """
    Validate and insert a new project configuration.

    Returns the created document (with project_id populated).
    Raises ValueError on validation errors or duplicate name.
    """
    cleaned = validate_project(data)

    ensure_indexes()
    col = get_collection("project_settings")
    doc = cleaned.copy()
    try:
        col.insert_one(doc)
    except DuplicateKeyError:
        raise ValueError(
            f"A project named '{cleaned['project_name']}' already exists."
        )

    return normalize_project(doc)


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
    col = get_collection("project_settings")
    existing = col.find_one({"_id": oid})
    if existing is None:
        raise ValueError("Project not found.")

    # Before validation, replace masked secrets with originals from DB
    _restore_masked_secrets(data, existing)

    cleaned = validate_project(data)

    try:
        result = col.replace_one({"_id": oid}, cleaned)
    except DuplicateKeyError:
        raise ValueError(
            f"A project named '{cleaned['project_name']}' already exists."
        )
    if result.matched_count == 0:
        raise ValueError(f"Project not found.")

    cleaned["_id"] = oid
    return normalize_project(cleaned)


def _restore_masked_secrets(data, existing):
    """Replace SECRET_MASK placeholders in data with actual values from the DB."""
    integrations = data.get("integrations")
    if not isinstance(integrations, dict):
        return

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

    col = get_collection("project_settings")
    result = col.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise ValueError("Project not found.")


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
    col = get_collection("project_settings")
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
    }
    return create_project(data)


# ---------------------------------------------------------------------------
# Chat Session CRUD
# ---------------------------------------------------------------------------

def normalize_chat_session(doc):
    """Convert a MongoDB chat_sessions document for display."""
    if not doc:
        return None
    created_at = doc.get("created_at", "")
    if hasattr(created_at, "strftime"):
        created_at = created_at.strftime("%Y-%m-%d %H:%M")
    agent_state = doc.get("agent_state")
    state_meta = {}
    if isinstance(agent_state, dict):
        state_meta = {
            "source": agent_state.get("source", ""),
            "version": agent_state.get("version", ""),
            "saved_at": agent_state.get("saved_at", ""),
        }
    return {
        "session_id": str(doc["_id"]),
        "project_id": doc.get("project_id", ""),
        "description": doc.get("description", ""),
        "created_at": created_at,
        "discussions": doc.get("discussions", []),
        "status": doc.get("status", "idle"),
        "current_round": doc.get("current_round", 0),
        "has_agent_state": isinstance(agent_state, dict) and isinstance(agent_state.get("state"), dict),
        "agent_state_meta": state_meta,
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
    return normalize_chat_session(doc)


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
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
    }
    payload_size = len(json.dumps(payload, ensure_ascii=True).encode("utf-8"))
    if payload_size > MAX_AGENT_STATE_BYTES:
        raise ValueError("Agent state is too large to persist.")

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


def delete_chat_session(session_id):
    """Delete a chat session by _id hex string. Raises ValueError if not found."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")
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
