"""
Trello service layer — session token CRUD, credential resolution,
and orchestration of trello_client calls.
"""

from datetime import datetime, timedelta, timezone

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
        f"?expiration=1hour"
        f"&name={app_name}"
        f"&scope=read,write"
        f"&response_type=token"
        f"&key={api_key}"
        f"&callback_method=fragment"
        f"&return_url={callback_url}"
    )


# ---------------------------------------------------------------------------
# Session token CRUD
# ---------------------------------------------------------------------------

def store_session_token(session_id, token):
    """
    Store a Trello token on the chat session document.

    Sets trello_token and trello_token_expiry (now + 1 hour).
    """
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    result = col.update_one(
        {"_id": oid},
        {"$set": {"trello_token": token, "trello_token_expiry": expiry}},
    )
    if result.matched_count == 0:
        raise ValueError("Chat session not found.")
    return {"expires_at": expiry.isoformat()}


def get_session_token(session_id):
    """
    Return {token, expiry} from the raw session document, or None.
    """
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return None

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    doc = col.find_one({"_id": oid}, {"trello_token": 1, "trello_token_expiry": 1})
    if not doc:
        return None

    token = doc.get("trello_token")
    expiry = doc.get("trello_token_expiry")
    if not token:
        return None

    return {"token": token, "expiry": expiry}


def is_token_valid(session_id):
    """Return True if the session has a non-expired Trello token."""
    info = get_session_token(session_id)
    if not info:
        return False
    expiry = info.get("expiry")
    if not expiry:
        return False
    if hasattr(expiry, "timestamp"):
        return expiry > datetime.now(timezone.utc)
    return False


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _resolve_credentials(session_id):
    """
    Load api_key from the project config and token from the session.

    Returns (api_key, token).
    Raises ValueError if token is missing/expired or api_key is missing.
    """
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid session ID '{session_id}'.")

    col = get_collection(CHAT_SESSIONS_COLLECTION)
    session_doc = col.find_one({"_id": oid})
    if not session_doc:
        raise ValueError("Chat session not found.")

    token = session_doc.get("trello_token")
    expiry = session_doc.get("trello_token_expiry")
    if not token:
        raise ValueError("No Trello token. Please authorize first.")
    if expiry and hasattr(expiry, "timestamp") and expiry <= datetime.now(timezone.utc):
        raise ValueError("Trello token expired. Please re-authorize.")

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
    api_key = (trello_cfg.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("Trello API key not configured in project.")

    return api_key, token


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

def run_export_extract(session_id):
    """
    Run extraction agent against the session discussion.

    Returns a list of extracted items.
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

    discussion = session_doc.get("discussion") or []
    discussion_text = "\n\n".join(
        f"**{m.get('agent_name', 'Unknown')}**: {m.get('content', '')}"
        for m in discussion
    )
    if not discussion_text.strip():
        raise ValueError("No discussion content to extract from.")

    from agents.integrations.extractor import run_extraction
    return run_extraction(system_prompt, discussion_text, project)


def run_export_push(session_id, list_id, items):
    """
    Push extracted items to Trello as cards on the given list.

    Returns result list from trello_client.push_cards.
    """
    if not items:
        raise ValueError("No items to export.")
    api_key, token = _resolve_credentials(session_id)
    return trello_client.push_cards(api_key, token, list_id, items)
