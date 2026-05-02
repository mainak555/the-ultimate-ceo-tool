"""
Jira service layer — credential resolution and orchestration of jira_client calls.

Three project sub-types: software, service_desk, business.
Each type has independent credentials (site_url, email, api_key).
Export payloads stored as discussions[].exports.jira.<type>.
"""

import logging
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId

from .db import CHAT_SESSIONS_COLLECTION, PROJECT_SETTINGS_COLLECTION, get_collection
from . import services
from . import jira_client
from . import jira_software_service
from . import jira_service_desk_service
from . import jira_business_service
from core.tracing import traced_function

logger = logging.getLogger(__name__)

JIRA_TYPES = ("software", "service_desk", "business")


def _coerce_confidence(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, out))


def _provider_key(type_name):
    """Return the Jira exports subkey for a given Jira type."""
    return (type_name or "").strip().lower()


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _get_project_for_session(session_id):
    """Return the raw project doc for a session's project_id."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    session_doc = col.find_one({"_id": oid})
    if not session_doc:
        raise ValueError("Chat session not found.")

    project_id = session_doc.get("project_id")
    if not project_id:
        raise ValueError("Session is not linked to a project.")

    project_col = get_collection(PROJECT_SETTINGS_COLLECTION)
    try:
        project_oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError("Invalid project ID on session.")

    project = project_col.find_one({"_id": project_oid})
    if not project:
        raise ValueError("Project not found.")
    return project


def _get_project_raw(project_id):
    """Return raw project doc by project_id."""
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid project ID '{project_id}'.")
    col = get_collection(PROJECT_SETTINGS_COLLECTION)
    project = col.find_one({"_id": oid})
    if not project:
        raise ValueError("Project not found.")
    return project


def _type_config_from_project(project, type_name):
    """Extract the jira sub-type config dict from a raw project doc."""
    integrations = project.get("integrations") or {}
    jira = integrations.get("jira") or {}
    return jira.get(type_name) or {}


def _resolve_type_credentials_from_project(project, type_name):
    """Return (site_url, email, api_key) from a raw project doc."""
    cfg = _type_config_from_project(project, type_name)
    site_url = (cfg.get("site_url") or "").strip()
    email = (cfg.get("email") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()

    if not site_url:
        raise ValueError(f"Jira site URL not configured for type '{type_name}'.")
    if not email:
        raise ValueError(f"Jira email not configured for type '{type_name}'.")
    if not api_key:
        raise ValueError(f"Jira API key not configured for type '{type_name}'.")
    return site_url, email, api_key


def _resolve_project_type_credentials(project_id, type_name):
    """Resolve (site_url, email, api_key) from a project_id."""
    project = _get_project_raw(project_id)
    return _resolve_type_credentials_from_project(project, type_name)


def _resolve_session_type_credentials(session_id, type_name):
    """Resolve (site_url, email, api_key) from a session's project."""
    project = _get_project_for_session(session_id)
    return _resolve_type_credentials_from_project(project, type_name)


def _get_session_project_id(session_id):
    """Return project_id string from a session."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")
    col = get_collection(CHAT_SESSIONS_COLLECTION)
    doc = col.find_one({"_id": oid}, {"project_id": 1})
    if not doc:
        raise ValueError("Chat session not found.")
    return doc.get("project_id", "")


# ---------------------------------------------------------------------------
# Type configuration checks
# ---------------------------------------------------------------------------

def is_type_configured(project_id, type_name):
    """Return True if all credentials are present for a given type."""
    try:
        project = _get_project_raw(project_id)
    except ValueError:
        return False
    try:
        _resolve_type_credentials_from_project(project, type_name)
        return True
    except ValueError:
        return False


def get_session_type_status(session_id, type_name):
    """Return {configured, default_project_key, default_project_name} for a session type."""
    try:
        project = _get_project_for_session(session_id)
    except ValueError:
        return {"configured": False, "default_project_key": "", "default_project_name": ""}

    integrations = project.get("integrations") or {}
    jira = integrations.get("jira") or {}
    type_cfg = jira.get(type_name) or {}

    try:
        _resolve_type_credentials_from_project(project, type_name)
        configured = True
    except ValueError:
        configured = False

    return {
        "configured": configured,
        "default_project_key": (type_cfg.get("default_project_key") or "").strip(),
        "default_project_name": (type_cfg.get("default_project_name") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Project-scoped API proxies (config page)
# ---------------------------------------------------------------------------

@traced_function("service.jira.verify_credentials")
def verify_project_type_credentials(project_id, type_name):
    """Verify Jira credentials for a project type. Returns user info dict."""
    site_url, email, api_key = _resolve_project_type_credentials(project_id, type_name)
    return jira_client.verify_credentials(site_url, email, api_key)


def fetch_project_spaces(project_id, type_name):
    """Fetch Jira projects for a project type (for config cascade)."""
    site_url, email, api_key = _resolve_project_type_credentials(project_id, type_name)
    if type_name == "service_desk":
        return jira_service_desk_service.fetch_spaces(site_url, email, api_key)
    if type_name == "software":
        return jira_software_service.fetch_spaces(site_url, email, api_key)
    if type_name == "business":
        return jira_business_service.fetch_spaces(site_url, email, api_key)
    else:
        raise ValueError(f"Unknown Jira type '{type_name}'.")


# ---------------------------------------------------------------------------
# Session-scoped API proxies (export modal)
# ---------------------------------------------------------------------------

def fetch_session_spaces(session_id, type_name):
    """Fetch Jira projects for a session type (for export modal)."""
    site_url, email, api_key = _resolve_session_type_credentials(session_id, type_name)
    if type_name == "service_desk":
        return jira_service_desk_service.fetch_spaces(site_url, email, api_key)
    if type_name == "software":
        return jira_software_service.fetch_spaces(site_url, email, api_key)
    if type_name == "business":
        return jira_business_service.fetch_spaces(site_url, email, api_key)
    else:
        raise ValueError(f"Unknown Jira type '{type_name}'.")


@traced_function("service.jira.fetch_metadata")
def fetch_session_project_metadata(session_id, type_name, project_key):
    """Fetch project metadata used by export editor dropdowns."""
    project_key = (project_key or "").strip()
    if not project_key:
        raise ValueError("'project_key' is required.")

    site_url, email, api_key = _resolve_session_type_credentials(session_id, type_name)
    if type_name == "software":
        return jira_software_service.fetch_project_metadata(site_url, email, api_key, project_key)
    if type_name == "service_desk":
        return {
            "issue_types": [],
            "priorities": [{"id": p, "name": p} for p in jira_client.PRIORITY_VALUES],
            "sprints": [],
            "epics": [],
        }
    if type_name == "business":
        return {
            "issue_types": [{"id": n, "name": n} for n in jira_client.ISSUE_TYPES.get("business", [])],
            "priorities": [{"id": p, "name": p} for p in jira_client.PRIORITY_VALUES],
            "sprints": [],
            "epics": [],
        }
    raise ValueError(f"Unknown Jira type '{type_name}'.")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _get_type_export_mapping(project, type_name):
    """Return (system_prompt, model, temperature) for a type's export_mapping."""
    integrations = project.get("integrations") or {}
    jira = integrations.get("jira") or {}
    type_cfg = jira.get(type_name) or {}
    mapping = type_cfg.get("export_mapping") or {}
    system_prompt = (mapping.get("system_prompt") or "").strip()
    model = (mapping.get("model") or "").strip()
    try:
        temperature = float(mapping.get("temperature") or 0.0)
    except (TypeError, ValueError):
        temperature = 0.0
    return system_prompt, model, temperature


@traced_function("service.jira.export.extract")
def run_export_extract(session_id, discussion_id, type_name):
    """
    Run extraction agent against a discussion, returning extracted issue items.

    Saves the result as the export payload and returns items list.
    """
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    discussion_id = (discussion_id or "").strip()
    if not discussion_id:
        raise ValueError("'discussion_id' is required.")

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    session_doc = col.find_one({"_id": oid})
    if not session_doc:
        raise ValueError("Chat session not found.")

    project_id = session_doc.get("project_id")
    if not project_id:
        raise ValueError("Session is not linked to a project.")

    project_col = get_collection(PROJECT_SETTINGS_COLLECTION)
    try:
        project_oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError("Invalid project ID on session.")

    project = project_col.find_one({"_id": project_oid})
    if not project:
        raise ValueError("Project not found.")

    integrations = project.get("integrations") or {}
    jira_cfg = integrations.get("jira") or {}
    if not jira_cfg.get("enabled"):
        raise ValueError("Jira is not enabled for this project.")

    type_cfg = jira_cfg.get(type_name) or {}
    if not type_cfg.get("enabled"):
        raise ValueError(f"Jira type '{type_name}' is not enabled for this project.")

    system_prompt, extraction_model, extraction_temperature = _get_type_export_mapping(project, type_name)

    discussions = session_doc.get("discussions") or []
    discussion_item = next(
        (m for m in discussions if isinstance(m, dict) and (m.get("id") or "").strip() == discussion_id),
        None,
    )
    if not discussion_item:
        raise ValueError("Discussion item not found for this session.")

    discussion_text = f"**{discussion_item.get('agent_name', 'Unknown')}**: {discussion_item.get('content', '')}"
    if not discussion_text.strip():
        raise ValueError("No discussion content to extract from.")

    from agents.integrations.extractor import run_extraction
    extracted = run_extraction(
        system_prompt,
        discussion_text,
        project,
        model=extraction_model,
        temperature=extraction_temperature,
    )

    saved = save_export(session_id, discussion_id, type_name, extracted, source="extract")
    return saved.get("issues") or []


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_export_items(items, type_name):
    """Normalize extracted/manual items into canonical schema for a Jira type."""
    if not isinstance(items, list):
        raise ValueError("'items' array is required")

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        if type_name == "software":
            normalized.append(
                jira_software_service.normalize_item(item, _normalize_labels, _coerce_confidence)
            )
        elif type_name == "service_desk":
            normalized.append(
                jira_service_desk_service.normalize_item(item, _normalize_labels, _coerce_confidence)
            )
        elif type_name == "business":
            normalized.append(
                jira_business_service.normalize_item(item, _normalize_labels, _coerce_confidence)
            )
        else:
            raise ValueError(f"Unknown Jira type '{type_name}'.")

    if type_name == "software":
        normalized = jira_software_service.repair_hierarchy(normalized)

    return normalized


def _normalize_labels(labels):
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


# ---------------------------------------------------------------------------
# Export payload persistence
# ---------------------------------------------------------------------------

def _build_export_payload(items, type_name, source):
    return {
        "schema_version": "2026-04-23",
        "updated_at": datetime.now(timezone.utc),
        "exported": False,
        "source": (source or "manual").strip() or "manual",
        "issues": normalize_export_items(items, type_name),
    }


def get_saved_export(session_id, discussion_id, type_name):
    """Return persisted Jira export payload for a discussion/type, if any."""
    return services.get_discussion_export_payload(
        session_id,
        discussion_id,
        "jira",
        subkey=_provider_key(type_name),
    )


def save_export(session_id, discussion_id, type_name, items, source="manual"):
    """Persist Jira export payload for a discussion/type and return saved payload."""
    payload = _build_export_payload(items, type_name, source)
    return services.set_discussion_export_payload(
        session_id,
        discussion_id,
        "jira",
        payload,
        subkey=_provider_key(type_name),
    )


def save_push_result(session_id, discussion_id, type_name, project_key, push_result):
    """Persist push result into existing Jira export payload."""
    payload = get_saved_export(session_id, discussion_id, type_name) or {
        "schema_version": "2026-04-23",
        "updated_at": datetime.now(timezone.utc),
        "exported": False,
        "source": "manual",
        "issues": [],
    }
    payload["last_push"] = {
        "pushed_at": datetime.now(timezone.utc),
        "project_key": project_key,
        "result": push_result,
    }
    payload["exported"] = True
    payload["updated_at"] = datetime.now(timezone.utc)
    return services.set_discussion_export_payload(
        session_id,
        discussion_id,
        "jira",
        payload,
        subkey=_provider_key(type_name),
    )


# ---------------------------------------------------------------------------
# Export push
# ---------------------------------------------------------------------------

@traced_function("service.jira.export.push")
def run_export_push(session_id, type_name, project_key, items):
    """Push issues to Jira and return result list."""
    normalized = normalize_export_items(items, type_name)
    if not normalized:
        raise ValueError("No items to export.")

    site_url, email, api_key = _resolve_session_type_credentials(session_id, type_name)

    if type_name == "software":
        return jira_software_service.push_issues(site_url, email, api_key, project_key, normalized)
    elif type_name == "service_desk":
        return jira_service_desk_service.push_issues(site_url, email, api_key, project_key, normalized)
    elif type_name == "business":
        return jira_business_service.push_issues(site_url, email, api_key, project_key, normalized)
    else:
        raise ValueError(f"Unknown Jira type '{type_name}'.")


# ---------------------------------------------------------------------------
# Reference markdown (shared — same discussion.content logic as Trello)
# ---------------------------------------------------------------------------

def get_discussion_reference_markdown(session_id, discussion_id):
    """Return raw discussion.content for reference pane rendering."""
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    target_discussion_id = (discussion_id or "").strip()
    if not target_discussion_id:
        raise ValueError("'discussion_id' is required.")

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    session_doc = col.find_one({"_id": oid}, {"discussions": 1})
    if not session_doc:
        raise ValueError("Chat session not found.")

    for row in session_doc.get("discussions") or []:
        if not isinstance(row, dict):
            continue
        if (row.get("id") or "").strip() != target_discussion_id:
            continue
        content = str(row.get("content") or "")
        return {
            "discussion_id": target_discussion_id,
            "agent_name": str(row.get("agent_name") or ""),
            "markdown": content,
        }

    raise ValueError("Discussion item not found for this session.")
