"""
Trello service layer — session token CRUD, credential resolution,
and orchestration of trello_client calls.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bson import ObjectId
from bson.errors import InvalidId

from .db import get_collection, CHAT_SESSIONS_COLLECTION
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
    return run_extraction(
        system_prompt,
        discussion_text,
        project,
        model=extraction_model,
        temperature=extraction_temperature,
    )


def run_export_push(session_id, list_id, items):
    """
    Push extracted items to Trello as cards on the given list.

    Returns result list from trello_client.push_cards.
    """
    if not items:
        raise ValueError("No items to export.")
    api_key, token = _resolve_credentials(session_id)
    return trello_client.push_cards(api_key, token, list_id, items)
