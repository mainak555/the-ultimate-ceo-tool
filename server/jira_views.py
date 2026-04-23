"""
Jira integration views — credential verification, API proxies, and export endpoints.

All views require the X-App-Secret-Key header.
"""

import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import services
from . import jira_service

VALID_JIRA_TYPES = frozenset(("software", "service_desk", "business"))


def _has_valid_secret(request):
    """Check the secret key passed in the request headers."""
    key = request.headers.get("X-App-Secret-Key", "").strip()
    return services.verify_secret_key(key)


def _json_response(data, status=200):
    return HttpResponse(json.dumps(data), status=status, content_type="application/json")


def _json_error(message, status=400):
    return _json_response({"error": message}, status=status)


def _validate_type(type_name):
    """Return None if valid, else a 400 error response."""
    if type_name not in VALID_JIRA_TYPES:
        return _json_error(
            f"Invalid Jira type '{type_name}'. Must be one of: software, service_desk, business."
        )
    return None


# ---------------------------------------------------------------------------
# Project-scoped views (config page)
# ---------------------------------------------------------------------------

@require_GET
def jira_project_verify(request, project_id, type_name):
    """GET — Verify Jira credentials for a project type."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    try:
        data = jira_service.verify_project_type_credentials(project_id, type_name)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response({"ok": True, "user": data})


@require_GET
def jira_project_spaces(request, project_id, type_name):
    """GET — List Jira projects (spaces) for a project type (config page cascade)."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    try:
        spaces = jira_service.fetch_project_spaces(project_id, type_name)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(spaces)


# ---------------------------------------------------------------------------
# Session-scoped views (export modal)
# ---------------------------------------------------------------------------

@require_GET
def jira_session_status(request, session_id, type_name):
    """GET — Check if a Jira type is configured for the session's project."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    try:
        status = jira_service.get_session_type_status(session_id, type_name)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(status)


@require_GET
def jira_session_spaces(request, session_id, type_name):
    """GET — List Jira projects for the session's project type (export modal)."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    try:
        spaces = jira_service.fetch_session_spaces(session_id, type_name)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(spaces)


@require_GET
def jira_session_metadata(request, session_id, type_name):
    """GET — Return project metadata for export editor dropdowns."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    project_key = (request.GET.get("project_key") or "").strip()
    if not project_key:
        return _json_error("'project_key' is required")

    try:
        data = jira_service.fetch_session_project_metadata(session_id, type_name, project_key)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@csrf_exempt
@require_POST
def jira_extract(request, session_id, discussion_id, type_name):
    """POST — Run extraction agent against a discussion message."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    try:
        items = jira_service.run_export_extract(session_id, discussion_id, type_name)
    except ValueError as e:
        return _json_error(str(e))
    except Exception as exc:
        return _json_error(f"Extraction failed: {exc}", 500)

    return _json_response({"items": items or []})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def jira_export_data(request, session_id, discussion_id, type_name):
    """GET — Load saved export. POST — Save edited export."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    if request.method == "GET":
        try:
            payload = jira_service.get_saved_export(session_id, discussion_id, type_name)
        except ValueError as e:
            return _json_error(str(e))

        if payload is None:
            return _json_response({"export": None, "saved": False})
        return _json_response({"export": payload, "saved": True})

    # POST — save
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error("Invalid JSON body")

    raw_items = body.get("items")
    if raw_items is None:
        raw_items = []
    source = (body.get("source") or "manual").strip() or "manual"

    try:
        saved = jira_service.save_export(session_id, discussion_id, type_name, raw_items, source=source)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response({"export": saved, "saved": True})


@require_GET
def jira_reference(request, session_id, discussion_id):
    """GET — Return raw discussion content as markdown for the reference pane."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    try:
        data = jira_service.get_discussion_reference_markdown(session_id, discussion_id)
    except ValueError as e:
        return _json_error(str(e))

    return _json_response(data)


@csrf_exempt
@require_POST
def jira_push(request, session_id, type_name):
    """POST — Push issues to Jira and save push result."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    err = _validate_type(type_name)
    if err:
        return err

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error("Invalid JSON body")

    project_key = (body.get("project_key") or "").strip()
    if not project_key:
        return _json_error("'project_key' is required")

    discussion_id = (body.get("discussion_id") or "").strip()
    raw_items = body.get("items")
    if raw_items is None:
        raw_items = []

    try:
        push_result = jira_service.run_export_push(session_id, type_name, project_key, raw_items)
    except ValueError as e:
        return _json_error(str(e))
    except Exception as exc:
        return _json_error(f"Push failed: {exc}", 500)

    if discussion_id:
        try:
            jira_service.save_push_result(session_id, discussion_id, type_name, project_key, push_result)
        except ValueError:
            pass  # Non-fatal — push already succeeded

    return _json_response({"result": push_result})
