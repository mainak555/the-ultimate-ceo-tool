"""
Trello service layer — session token CRUD, credential resolution,
and orchestration of trello_client calls.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bson import ObjectId
from bson.errors import InvalidId

from .db import get_collection, CHAT_SESSIONS_COLLECTION
from . import services
from . import trello_client


# ---------------------------------------------------------------------------
# Auth URL
# ---------------------------------------------------------------------------

def build_auth_url(project, callback_url):
    """
    Build the Trello authorization URL from the project's raw config.

    Uses fragment callback: Trello redirects to callback_url#token=<token>.
    The callback page reads the hash and relays it to the opener via postMessage.

    Returns the full authorize URL string.
    Raises ValueError if api_key or app_name is missing.
    """
    integrations = project.get("integrations") or {}
    trello = integrations.get("trello") or {}
    api_key = (trello.get("api_key") or "").strip()
    app_name = (trello.get("app_name") or "ProductDiscovery").strip()

    if not api_key:
        raise ValueError("Trello API key is not configured for this project.")

    return (
        "https://trello.com/1/authorize"
        f"?expiration=never"
        f"&name={app_name}"
        f"&scope=read,write"
        f"&response_type=token"
        f"&key={api_key}"
        f"&callback_method=fragment"
        f"&return_url={quote(callback_url, safe='')}"
    )


# ---------------------------------------------------------------------------
# Project-level token CRUD
# ---------------------------------------------------------------------------

def store_project_token(project_id, token):
    """
    Store a Trello token on the project config document.

    Sets integrations.trello.token and integrations.trello.token_generated_at.
    """
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid project ID '{project_id}'.")

    col = get_collection("project_settings")
    now = datetime.now(timezone.utc).isoformat()
    result = col.update_one(
        {"_id": oid},
        {"$set": {
            "integrations.trello.token": token,
            "integrations.trello.token_generated_at": now,
        }},
    )
    if result.matched_count == 0:
        raise ValueError("Project not found.")
    return {"token_generated_at": now}


def get_project_token(project_id):
    """Return {token, token_generated_at} from the project, or None."""
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        return None

    col = get_collection("project_settings")
    doc = col.find_one({"_id": oid}, {"integrations.trello.token": 1, "integrations.trello.token_generated_at": 1})
    if not doc:
        return None

    trello = (doc.get("integrations") or {}).get("trello") or {}
    token = trello.get("token")
    if not token:
        return None
    return {"token": token, "token_generated_at": trello.get("token_generated_at", "")}


def is_project_token_valid(project_id):
    """Return True if the project has a Trello token configured."""
    info = get_project_token(project_id)
    return bool(info and info.get("token"))


def _resolve_project_credentials(project_id):
    """
    Load api_key and token from the project config.

    Returns (api_key, token).
    Raises ValueError if token or api_key is missing.
    """
    try:
        project_oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError("Invalid project ID.")

    col = get_collection("project_settings")
    project = col.find_one({"_id": project_oid})
    if not project:
        raise ValueError("Project not found.")

    integrations = project.get("integrations") or {}
    trello_cfg = integrations.get("trello") or {}
    api_key = (trello_cfg.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("Trello API key not configured in project.")

    token = (trello_cfg.get("token") or "").strip()
    if not token:
        raise ValueError("No Trello token. Please generate one in project settings.")

    return api_key, token


def build_project_auth_url(project_id, callback_url):
    """Build the Trello auth URL for a project (expiration=never)."""
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError("Invalid project ID.")

    col = get_collection("project_settings")
    project = col.find_one({"_id": oid})
    if not project:
        raise ValueError("Project not found.")

    return build_auth_url(project, callback_url)


# ---------------------------------------------------------------------------
# Project-scoped proxy operations
# ---------------------------------------------------------------------------

def fetch_project_workspaces(project_id):
    """Fetch Trello workspaces using project credentials."""
    api_key, token = _resolve_project_credentials(project_id)
    return trello_client.get_workspaces(api_key, token)


def fetch_project_boards(project_id, workspace_id=None):
    """Fetch boards using project credentials."""
    api_key, token = _resolve_project_credentials(project_id)
    return trello_client.get_boards(api_key, token, workspace_id)


def fetch_project_lists(project_id, board_id):
    """Fetch lists using project credentials."""
    api_key, token = _resolve_project_credentials(project_id)
    return trello_client.get_lists(api_key, token, board_id)


def create_project_board(project_id, name, workspace_id=None):
    """Create a new board using project credentials."""
    api_key, token = _resolve_project_credentials(project_id)
    return trello_client.create_board(api_key, token, name, workspace_id)


def create_project_list(project_id, name, board_id):
    """Create a new list using project credentials."""
    api_key, token = _resolve_project_credentials(project_id)
    return trello_client.create_list(api_key, token, name, board_id)

def _resolve_credentials(session_id):
    """
    Load api_key and token from the project config (via the session's project_id).

    Returns (api_key, token).
    Raises ValueError if token is missing or api_key is missing.
    """
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

    return _resolve_project_credentials(project_id)


# ---------------------------------------------------------------------------
# Proxy operations (delegate to trello_client)
# ---------------------------------------------------------------------------

def fetch_workspaces(session_id):
    """Fetch Trello workspaces for the authenticated user."""
    api_key, token = _resolve_credentials(session_id)
    return trello_client.get_workspaces(api_key, token)


def fetch_boards(session_id, workspace_id=None):
    """Fetch boards, optionally scoped to a workspace."""
    api_key, token = _resolve_credentials(session_id)
    return trello_client.get_boards(api_key, token, workspace_id)


def fetch_lists(session_id, board_id):
    """Fetch lists for a board."""
    api_key, token = _resolve_credentials(session_id)
    return trello_client.get_lists(api_key, token, board_id)


def create_board(session_id, name, workspace_id=None):
    """Create a new board."""
    api_key, token = _resolve_credentials(session_id)
    return trello_client.create_board(api_key, token, name, workspace_id)


def create_list(session_id, name, board_id):
    """Create a new list on a board."""
    api_key, token = _resolve_credentials(session_id)
    return trello_client.create_list(api_key, token, name, board_id)


# ---------------------------------------------------------------------------
# Export operations
# ---------------------------------------------------------------------------

PRIORITY_VALUES = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Critical"}


def _utc_iso_now():
    return datetime.now(timezone.utc).isoformat()


def _coerce_confidence(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, out))


def _normalize_labels(labels):
    if not isinstance(labels, list):
        return []
    seen = set()
    cleaned = []
    for label in labels:
        text = str(label or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _normalize_custom_fields(custom_fields):
    if not isinstance(custom_fields, list):
        return []

    normalized = []
    for row in custom_fields:
        if not isinstance(row, dict):
            continue
        field_name = str(row.get("field_name") or "").strip()
        if not field_name:
            continue
        field_type = (str(row.get("field_type") or "text").strip() or "text").lower()
        if field_type != "text":
            field_type = "text"
        normalized.append({
            "field_name": field_name,
            "field_type": field_type,
            "value": str(row.get("value") or "").strip(),
        })
    return normalized


def _normalize_checklists(item):
    checklists = item.get("checklists")
    if isinstance(checklists, list):
        normalized = []
        for row in checklists:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "Tasks").strip() or "Tasks"
            raw_items = row.get("items")
            list_items = []
            if isinstance(raw_items, list):
                for child in raw_items:
                    if not isinstance(child, dict):
                        continue
                    title = str(child.get("title") or "").strip()
                    if not title:
                        continue
                    list_items.append({
                        "title": title,
                        "checked": bool(child.get("checked", False)),
                    })
            if list_items:
                normalized.append({"name": name, "items": list_items})
        return normalized

    # Backward compatibility for the legacy extraction schema.
    children = item.get("children")
    if isinstance(children, list) and children:
        list_items = []
        for child in children:
            if not isinstance(child, dict):
                continue
            title = str(child.get("title") or "").strip()
            if not title:
                continue
            list_items.append({"title": title, "checked": False})
        if list_items:
            return [{"name": "Tasks", "items": list_items}]

    return []


def normalize_export_items(items):
    """Normalize extractor/manual payloads into canonical Trello card schema."""
    if not isinstance(items, list):
        raise ValueError("'items' array is required")

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        card_title = str(item.get("card_title") or item.get("title") or "").strip() or "Untitled"
        card_description = str(
            item.get("card_description")
            if item.get("card_description") is not None
            else item.get("description")
            or ""
        ).strip()

        priority_raw = str(item.get("priority") or "").strip().lower()
        priority = PRIORITY_VALUES.get(priority_raw, "")

        normalized.append({
            "card_title": card_title,
            "card_description": card_description,
            "checklists": _normalize_checklists(item),
            "custom_fields": _normalize_custom_fields(item.get("custom_fields")),
            "labels": _normalize_labels(item.get("labels")),
            "priority": priority,
            "confidence_score": _coerce_confidence(item.get("confidence_score", 0.0)),
        })

    return normalized


def _build_export_payload(items, source):
    return {
        "schema_version": "2026-04-21",
        "updated_at": _utc_iso_now(),
        "source": (source or "manual").strip() or "manual",
        "cards": normalize_export_items(items),
    }


def get_saved_export(session_id, discussion_id):
    """Return persisted trello export payload for a discussion, if any."""
    return services.get_discussion_export_payload(session_id, discussion_id, "trello")


def save_export(session_id, discussion_id, items, source="manual"):
    """Persist trello export payload for a discussion and return saved payload."""
    payload = _build_export_payload(items, source)
    return services.set_discussion_export_payload(session_id, discussion_id, "trello", payload)


def save_push_result(session_id, discussion_id, list_id, push_result):
    """Persist push outcome into existing trello export payload."""
    payload = get_saved_export(session_id, discussion_id) or {
        "schema_version": "2026-04-21",
        "updated_at": _utc_iso_now(),
        "source": "manual",
        "cards": [],
    }
    payload["last_push"] = {
        "pushed_at": _utc_iso_now(),
        "list_id": list_id,
        "result": push_result,
    }
    payload["updated_at"] = _utc_iso_now()
    return services.set_discussion_export_payload(session_id, discussion_id, "trello", payload)


def run_export_extract(session_id, discussion_id):
    """
    Run extraction agent against a selected discussion message.

    Returns a list of extracted items.
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

    from .db import get_collection as _gc
    project_col = _gc("project_settings")
    try:
        project_oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise ValueError("Invalid project ID on session.")

    project = project_col.find_one({"_id": project_oid})
    if not project:
        raise ValueError("Project not found.")

    integrations = project.get("integrations") or {}
    trello_cfg = integrations.get("trello") or {}
    if not trello_cfg.get("enabled"):
        raise ValueError("Trello is not enabled for this project.")

    mapping = trello_cfg.get("export_mapping") or {}
    system_prompt = mapping.get("system_prompt", "")
    extraction_model = (mapping.get("model") or "").strip()
    extraction_temperature = float(mapping.get("temperature") or 0.0)

    discussions = session_doc.get("discussions") or []
    discussion_item = next(
        (
            m for m in discussions
            if isinstance(m, dict) and (m.get("id") or "").strip() == discussion_id
        ),
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
    saved = save_export(session_id, discussion_id, extracted, source="extract")
    return saved.get("cards") or []


def run_export_push(session_id, list_id, items):
    """
    Push extracted items to Trello as cards on the given list.

    Returns result list from trello_client.push_cards.
    """
    normalized = normalize_export_items(items)
    if not normalized:
        raise ValueError("No items to export.")
    api_key, token = _resolve_credentials(session_id)
    return trello_client.push_cards(api_key, token, list_id, normalized)
