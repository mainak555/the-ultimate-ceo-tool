"""
Business logic layer — pure functions operating on dicts.

No request/response objects here. Views call these functions
and translate results into HTTP/HTMX responses.
"""

import hmac
import os
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from .db import get_collection, ensure_indexes, CHAT_SESSIONS_COLLECTION
from .model_catalog import get_agent_model_names, get_default_system_prompt
from .schemas import validate_project, validate_chat_session


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
    return get_default_system_prompt()


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
    }

    return {
        "project_id": str(project["_id"]) if project.get("_id") else "",
        "project_name": project.get("project_name", ""),
        "objective": project.get("objective", ""),
        "agents": assistants,
        "human_gate": human_gate,
        "team": team,
    }


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

def list_projects():
    """Return all project settings, sorted by project_name ascending."""
    ensure_indexes()
    col = get_collection("project_settings")
    cursor = col.find({}).sort("project_name", 1)
    return [normalize_project(p) for p in cursor]


def get_project(project_id):
    """Return a single project by _id (hex string), or None if not found."""
    ensure_indexes()
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        return None
    col = get_collection("project_settings")
    project = col.find_one({"_id": oid})
    return normalize_project(project)


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

    cleaned = validate_project(data)

    ensure_indexes()
    col = get_collection("project_settings")
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


def delete_project(project_id):
    """Delete a project by _id (hex string). Raises ValueError if not found."""
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid project ID '{project_id}'.")

    ensure_indexes()
    col = get_collection("project_settings")
    result = col.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise ValueError("Project not found.")


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
    return {
        "session_id": str(doc["_id"]),
        "project_id": doc.get("project_id", ""),
        "description": doc.get("description", ""),
        "created_at": created_at,
        "discussion": doc.get("discussion", []),
    }


def create_chat_session(project_id, description):
    """Insert a new chat session. Returns the normalized document."""
    cleaned = validate_chat_session({"project_id": project_id, "description": description})
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    doc = {
        "project_id": cleaned["project_id"],
        "description": cleaned["description"],
        "created_at": datetime.now(timezone.utc),
        "discussion": [],
    }
    col.insert_one(doc)
    return normalize_chat_session(doc)


def list_chat_sessions(project_id):
    """Return all sessions for a project, sorted newest first."""
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    cursor = col.find({"project_id": project_id}).sort("created_at", -1)
    return [normalize_chat_session(d) for d in cursor]


def get_chat_session(session_id):
    """Return a single chat session by _id hex string, or None."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return None
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    return normalize_chat_session(col.find_one({"_id": oid}))


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
