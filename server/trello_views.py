"""
Trello integration views — token management, API proxies, and export endpoints.

All views require the X-App-Secret-Key header.
"""

import json
from urllib.parse import quote as _urlquote

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

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
    GET — Callback page for Trello fragment auth.

    Accepts query params:
      - ``sid`` (session ID) + ``skey`` for session-scoped token storage
      - ``pid`` (project ID) + ``skey`` for project-scoped token storage
    Falls back to postMessage when params are absent.
    """
    project_id = request.GET.get("pid", "")
    secret_key = request.GET.get("skey", "")

    # Determine store URL
    if project_id and secret_key:
        store_url = f"/trello/project/{project_id}/store-token/"
        store_id = project_id
    else:
        store_url = ""
        store_id = ""

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
        '  var storeUrl = ' + json.dumps(store_url) + ';'
        '  var secretKey = ' + json.dumps(secret_key) + ';'
        '  if (storeUrl && secretKey) {'
        '    fetch(storeUrl, {'
        '      method: "POST",'
        '      headers: {'
        '        "Content-Type": "application/json",'
        '        "X-App-Secret-Key": secretKey'
        '      },'
        '      body: JSON.stringify({token: token})'
        '    }).then(function(r) {'
        '      if (!r.ok) {'
        '        return r.json().then(function(d) {'
        '          document.getElementById("msg").textContent = "Error: " + (d.error || "HTTP " + r.status) + ". Please close and retry.";'
        '        }).catch(function() {'
        '          document.getElementById("msg").textContent = "Error storing token (HTTP " + r.status + "). Please close and retry.";'
        '        });'
        '      }'
        '      if (window.opener) {'
        '        try { window.opener.postMessage("trello_token_stored", window.location.origin); } catch(e) {}'
        '      }'
        '      document.getElementById("msg").textContent = "Authorized! Closing\u2026";'
        '      setTimeout(function(){ window.close(); }, 600);'
        '    }).catch(function(err) {'
        '      document.getElementById("msg").textContent = "Network error: " + err.message + ". Please close and retry.";'
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
def trello_token_status(request, session_id):
    """GET — Check if the session's project has a valid Trello token."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    # Resolve project from session
    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)

    project_id = session.get("project_id", "")
    valid = trello_service.is_project_token_valid(project_id)
    info = trello_service.get_project_token(project_id)
    token_generated_at = info.get("token_generated_at", "") if info else ""

    # Include default selections for pre-populating export modal
    project = services.get_project(project_id) if project_id else None
    trello_cfg = (project or {}).get("integrations", {}).get("trello", {})
    defaults = {
        "default_workspace_id": trello_cfg.get("default_workspace_id", ""),
        "default_board_id": trello_cfg.get("default_board_id", ""),
        "default_list_id": trello_cfg.get("default_list_id", ""),
    }

    return _json_response({"valid": valid, "token_generated_at": token_generated_at, "defaults": defaults})


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
def trello_extract(request, session_id, discussion_id):
    """POST — Run extraction agent against a selected discussion message."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        items = trello_service.run_export_extract(session_id, discussion_id)
    except ValueError as e:
        return _json_error(str(e))
    except Exception as exc:
        return _json_error(f"Extraction failed: {exc}", 500)

    return _json_response({"items": items})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def trello_export_data(request, session_id, discussion_id):
    """GET/POST — Load or save persisted Trello export JSON for a discussion."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    if request.method == "GET":
        try:
            payload = trello_service.get_saved_export(session_id, discussion_id)
        except ValueError as e:
            return _json_error(str(e))
        return _json_response({"saved": bool(payload), "export": payload or {"cards": []}})

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error("Invalid JSON body")

    items = body.get("items")
    source = (body.get("source") or "manual").strip() or "manual"
    if not isinstance(items, list):
        return _json_error("'items' array is required")

    try:
        payload = trello_service.save_export(session_id, discussion_id, items, source=source)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response({"status": "ok", "export": payload})


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
    discussion_id = (body.get("discussion_id") or "").strip()
    items = body.get("items")

    if not list_id:
        return _json_error("'list_id' is required")
    if not isinstance(items, list) or not items:
        return _json_error("'items' array is required")

    try:
        result = trello_service.run_export_push(session_id, list_id, items)
        if discussion_id:
            trello_service.save_push_result(session_id, discussion_id, list_id, result)
    except ValueError as e:
        return _json_error(str(e))
    except Exception as exc:
        return _json_error(f"Export failed: {exc}", 500)

    return _json_response({"status": "ok", "result": result})


# ---------------------------------------------------------------------------
# Project-scoped Trello endpoints (used by config page)
# ---------------------------------------------------------------------------

@require_GET
def trello_project_auth_url(request, project_id):
    """GET — Return the Trello authorization URL for a project (expiration=never)."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    secret_key = request.headers.get("X-App-Secret-Key", "").strip()
    callback_url = request.build_absolute_uri(
        f"/trello/callback/?pid={project_id}&skey={_urlquote(secret_key, safe='')}"
    )

    try:
        url = trello_service.build_project_auth_url(project_id, callback_url)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response({"url": url})


@csrf_exempt
@require_POST
def trello_project_store_token(request, project_id):
    """POST — Store a Trello token on the project config."""
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
        result = trello_service.store_project_token(project_id, token)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response({"status": "ok", "token_generated_at": result["token_generated_at"]})


@require_GET
def trello_project_token_status(request, project_id):
    """GET — Check if the project has a valid Trello token."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    valid = trello_service.is_project_token_valid(project_id)
    info = trello_service.get_project_token(project_id)
    token_generated_at = info.get("token_generated_at", "") if info else ""

    return _json_response({"valid": valid, "token_generated_at": token_generated_at})


@require_GET
def trello_project_workspaces(request, project_id):
    """GET — List Trello workspaces using project credentials."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        data = trello_service.fetch_project_workspaces(project_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@require_GET
def trello_project_boards(request, project_id):
    """GET — List boards using project credentials, optionally filtered by ?workspace=<id>."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    workspace_id = request.GET.get("workspace", "").strip() or None

    try:
        data = trello_service.fetch_project_boards(project_id, workspace_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@require_GET
def trello_project_lists(request, project_id):
    """GET — List lists using project credentials (?board=<id>)."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    board_id = request.GET.get("board", "").strip()
    if not board_id:
        return _json_error("'board' query parameter is required")

    try:
        data = trello_service.fetch_project_lists(project_id, board_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@csrf_exempt
@require_POST
def trello_project_create_board(request, project_id):
    """POST — Create a new Trello board using project credentials."""
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
        data = trello_service.create_project_board(project_id, name, workspace_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@csrf_exempt
@require_POST
def trello_project_create_list(request, project_id):
    """POST — Create a new Trello list on a board using project credentials."""
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
        data = trello_service.create_project_list(project_id, name, board_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)
