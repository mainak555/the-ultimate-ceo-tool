"""
Trello integration views — token management, API proxies, and export endpoints.

All views require the X-App-Secret-Key header.
"""

import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import services
from . import trello_service


def _has_valid_secret(request):
    """Check the secret key passed in the request headers."""
    key = request.headers.get("X-App-Secret-Key", "").strip()
    return services.verify_secret_key(key)


def _json_response(data, status=200):
    return HttpResponse(json.dumps(data), status=status, content_type="application/json")


def _json_error(message, status=400):
    return _json_response({"error": message}, status=status)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

@require_GET
def trello_callback(request):
    """
    GET \u2014 Callback page for Trello fragment auth.

    Accepts query params ``sid`` (session ID) and ``skey`` (secret key) so the
    page can store the token server-side directly via fetch POST, eliminating
    the need for window.opener.postMessage (which breaks after cross-origin
    navigation through trello.com).  Falls back to postMessage when the params
    are absent.
    """
    session_id = request.GET.get("sid", "")
    secret_key = request.GET.get("skey", "")

    html = (
        '<!DOCTYPE html>'
        '<html><head><title>Trello Authorization</title></head>'
        '<body>'
        '<p id="msg">Authorization complete \u2014 closing\u2026</p>'
        '<script>'
        '(function(){'
        '  var hash = window.location.hash || "";'
        '  var token = "";'
        '  if (hash.indexOf("#token=") === 0) { token = hash.substring(7); }'
        '  else if (hash.indexOf("token=") !== -1) {'
        '    var m = hash.match(/token=([^&]+)/);'
        '    if (m) token = m[1];'
        '  }'
        '  if (!token) {'
        '    document.getElementById("msg").textContent = "No token received. You can close this window.";'
        '    return;'
        '  }'
        '  var sessionId = "' + session_id.replace('"', '') + '";'
        '  var secretKey = "' + secret_key.replace('"', '') + '";'
        '  if (sessionId && secretKey) {'
        '    fetch("/trello/" + sessionId + "/store-token/", {'
        '      method: "POST",'
        '      headers: {'
        '        "Content-Type": "application/json",'
        '        "X-App-Secret-Key": secretKey'
        '      },'
        '      body: JSON.stringify({token: token})'
        '    }).then(function() {'
        '      if (window.opener) {'
        '        try { window.opener.postMessage("trello_token_stored", window.location.origin); } catch(e) {}'
        '      }'
        '      document.getElementById("msg").textContent = "Authorized! Closing\u2026";'
        '      setTimeout(function(){ window.close(); }, 600);'
        '    }).catch(function() {'
        '      document.getElementById("msg").textContent = "Error storing token. Please close and retry.";'
        '    });'
        '  } else {'
        '    if (window.opener) {'
        '      try { window.opener.postMessage(token, window.location.origin); } catch(e) {}'
        '    }'
        '    setTimeout(function(){ window.close(); }, 3000);'
        '  }'
        '})();'
        '</script>'
        '</body></html>'
    )
    return HttpResponse(html, content_type='text/html')


@require_GET
def trello_auth_url(request, session_id):
    """GET — Return the Trello authorization URL for this session's project."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)

    project = services.get_project_raw(session["project_id"])
    if project is None:
        return _json_error("Project not found", 404)

    secret_key = request.headers.get("X-App-Secret-Key", "").strip()
    callback_url = request.build_absolute_uri(
        f"/trello/callback/?sid={session_id}&skey={secret_key}"
    )

    try:
        url = trello_service.build_auth_url(project, callback_url)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response({"url": url})


@csrf_exempt
@require_POST
def trello_store_token(request, session_id):
    """POST — Store a Trello token on the chat session."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error("Invalid JSON body")

    token = (body.get("token") or "").strip()
    if not token:
        return _json_error("'token' is required")

    try:
        result = trello_service.store_session_token(session_id, token)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response({"status": "ok", "expires_at": result["expires_at"]})


@require_GET
def trello_token_status(request, session_id):
    """GET — Check if the session has a valid Trello token."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    valid = trello_service.is_token_valid(session_id)
    info = trello_service.get_session_token(session_id)
    expires_at = None
    if info and info.get("expiry"):
        expiry = info["expiry"]
        expires_at = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)

    return _json_response({"valid": valid, "expires_at": expires_at})


# ---------------------------------------------------------------------------
# Trello API proxies
# ---------------------------------------------------------------------------

@require_GET
def trello_workspaces(request, session_id):
    """GET — List Trello workspaces."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        data = trello_service.fetch_workspaces(session_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@require_GET
def trello_boards(request, session_id):
    """GET — List boards, optionally filtered by ?workspace=<id>."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    workspace_id = request.GET.get("workspace", "").strip() or None

    try:
        data = trello_service.fetch_boards(session_id, workspace_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@require_GET
def trello_lists(request, session_id):
    """GET — List lists for a board (?board=<id>)."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    board_id = request.GET.get("board", "").strip()
    if not board_id:
        return _json_error("'board' query parameter is required")

    try:
        data = trello_service.fetch_lists(session_id, board_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@csrf_exempt
@require_POST
def trello_create_board(request, session_id):
    """POST — Create a new Trello board."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error("Invalid JSON body")

    name = (body.get("name") or "").strip()
    if not name:
        return _json_error("'name' is required")

    workspace_id = (body.get("workspace_id") or "").strip() or None

    try:
        data = trello_service.create_board(session_id, name, workspace_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@csrf_exempt
@require_POST
def trello_create_list(request, session_id):
    """POST — Create a new Trello list on a board."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error("Invalid JSON body")

    name = (body.get("name") or "").strip()
    board_id = (body.get("board_id") or "").strip()
    if not name:
        return _json_error("'name' is required")
    if not board_id:
        return _json_error("'board_id' is required")

    try:
        data = trello_service.create_list(session_id, name, board_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


# ---------------------------------------------------------------------------
# Export — extract & push
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def trello_extract(request, session_id):
    """POST — Run extraction agent against the session discussion."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        items = trello_service.run_export_extract(session_id)
    except ValueError as e:
        return _json_error(str(e))
    except Exception as exc:
        return _json_error(f"Extraction failed: {exc}", 500)

    return _json_response({"items": items})


@csrf_exempt
@require_POST
def trello_push(request, session_id):
    """POST — Push extracted items to Trello."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error("Invalid JSON body")

    list_id = (body.get("list_id") or "").strip()
    items = body.get("items")

    if not list_id:
        return _json_error("'list_id' is required")
    if not isinstance(items, list) or not items:
        return _json_error("'items' array is required")

    try:
        result = trello_service.run_export_push(session_id, list_id, items)
    except ValueError as e:
        return _json_error(str(e))
    except Exception as exc:
        return _json_error(f"Export failed: {exc}", 500)

    return _json_response({"status": "ok", "result": result})
