"""
Remote-user invitation and join views.

Host-facing (secret-gated):
  POST /chat/sessions/<session_id>/remote-users/<user_name>/invite/
      Generates (or re-generates) an invitation token and returns the join URL.

  POST /chat/sessions/<session_id>/remote-users/<user_name>/ignore/
      Marks a remote user as ignored for this run (host un-checked the checkbox).
      Revokes the existing token so the user's WebSocket is evicted.

  POST /chat/sessions/<session_id>/remote-users/<user_name>/unignore/
      Restores a previously ignored user to 'offline' so they can rejoin.

Remote-user-facing (public, token-gated):
  GET  /remote/join/<token>/
      Renders the standalone remote-user chat page. Public — no secret key.

  POST /remote/join/<token>/online/
      Called by remote_user.js immediately after the page loads.
      Validates the token, marks the user online, and publishes a readiness event.
      If all required users are now online/ignored, publishes 'complete' so
      the host's WebSocket triggers auto-continue.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from agents.session_coordination import (
    generate_remote_user_token,
    get_remote_user_statuses,
    get_remote_user_token_data,
    publish_remote_user_event,
    revoke_remote_user_token,
    set_remote_user_ignored,
    set_remote_user_offline,
    set_remote_user_online,
    set_session_quorum,
    SessionCoordinationError,
)
from . import services, util
from .views import _has_valid_secret

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Host-facing views (secret-gated)
# ---------------------------------------------------------------------------


@require_POST
def generate_invite_link(request, session_id, user_name):
    """Generate (or re-generate) an invitation token for a remote user."""
    if not _has_valid_secret(request):
        return util.json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return util.json_error("Session not found", 404)

    project = services.get_project(session["project_id"])
    if project is None:
        return util.json_error("Project not found", 404)

    # Verify user_name is a configured remote user.
    remote_users_cfg = (project.get("human_gate") or {}).get("remote_users") or []
    configured_names = [r["name"] for r in remote_users_cfg if isinstance(r, dict) and r.get("name")]
    if user_name not in configured_names:
        return util.json_error("Remote user not configured for this project.", 404)

    try:
        token = generate_remote_user_token(session_id, user_name, str(session["project_id"]))
    except SessionCoordinationError as exc:
        logger.error(
            "agents.remote_user.invite_error",
            extra={"session_id": session_id, "user_name": user_name, "error": str(exc)},
        )
        return util.json_error("Unable to generate invitation link.", 503)

    join_url = request.build_absolute_uri(f"/remote/join/{token}/")
    logger.info(
        "agents.remote_user.invite_generated",
        extra={"session_id": session_id, "user_name": user_name},
    )
    return util.json_response({"join_url": join_url, "user_name": user_name})


@require_POST
def ignore_remote_user(request, session_id, user_name):
    """Mark a remote user as ignored (host un-checked their checkbox)."""
    if not _has_valid_secret(request):
        return util.json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return util.json_error("Session not found", 404)

    try:
        # Revoke the token so any active remote WebSocket gets evicted.
        revoke_remote_user_token(session_id, user_name)
        set_remote_user_ignored(session_id, user_name)
    except SessionCoordinationError as exc:
        logger.error(
            "agents.remote_user.ignore_error",
            extra={"session_id": session_id, "user_name": user_name, "error": str(exc)},
        )
        return util.json_error("Unable to update remote user status.", 503)

    # Check if all required users are now satisfied → send complete to host WS.
    _maybe_publish_complete(session, session_id)

    return util.json_response({"status": "ignored", "user_name": user_name})


@require_POST
def unignore_remote_user(request, session_id, user_name):
    """Restore a previously ignored user to 'offline' (host re-checked the checkbox)."""
    if not _has_valid_secret(request):
        return util.json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return util.json_error("Session not found", 404)

    try:
        set_remote_user_offline(session_id, user_name)
    except SessionCoordinationError as exc:
        logger.error(
            "agents.remote_user.unignore_error",
            extra={"session_id": session_id, "user_name": user_name, "error": str(exc)},
        )
        return util.json_error("Unable to update remote user status.", 503)

    return util.json_response({"status": "offline", "user_name": user_name})


# ---------------------------------------------------------------------------
# Remote-user-facing views (public, token-gated)
# ---------------------------------------------------------------------------


@require_GET
def remote_user_join(request, token):
    """Render the standalone remote-user chat page.

    This view is PUBLIC — no APP_SECRET_KEY check. Access is guarded by the
    token alone (UUID4, stored in Redis with TTL).
    """
    token_data = _validate_token(token)
    if token_data is None:
        return render(request, "server/remote_user.html", {
            "error": "This invitation link has expired or is invalid.",
        })

    session_id = token_data["session_id"]
    user_name = token_data["user_name"]
    project_id = token_data["project_id"]

    session = services.get_chat_session(session_id)
    if session is None:
        return render(request, "server/remote_user.html", {
            "error": "Session not found.",
        })

    project = services.get_project(project_id)
    project_name = project.get("project_name", "") if project else ""

    # Load existing discussions for initial render.
    discussions = [
        d for d in (session.get("discussions") or [])
        if isinstance(d, dict)
    ]

    return render(request, "server/remote_user.html", {
        "token": token,
        "session_id": session_id,
        "user_name": user_name,
        "project_name": project_name,
        "discussions": discussions,
        "error": None,
    })


@csrf_exempt
@require_POST
def remote_user_mark_online(request, token):
    """Called by remote_user.js on page load to mark the user online.

    Sets the user's status in Redis and publishes an update event. If the
    update brings all required users online/ignored, publishes a 'complete'
    event so the host's WebSocket triggers auto-continue.
    """
    token_data = _validate_token(token)
    if token_data is None:
        return util.json_error("Invalid or expired invitation token.", 403)

    session_id = token_data["session_id"]
    user_name = token_data["user_name"]
    project_id = token_data["project_id"]

    session = services.get_chat_session(session_id)
    if session is None:
        return util.json_error("Session not found.", 404)

    project = services.get_project(project_id)
    if project is None:
        return util.json_error("Project not found.", 404)

    try:
        set_remote_user_online(session_id, user_name)
    except SessionCoordinationError as exc:
        logger.error(
            "agents.remote_user.mark_online_error",
            extra={"session_id": session_id, "user_name": user_name, "error": str(exc)},
        )
        return util.json_error("Unable to update remote user status.", 503)

    # Check if all required users are now satisfied → send complete to host WS.
    _maybe_publish_complete(project, session_id)

    return util.json_response({"status": "online", "user_name": user_name, "session_id": session_id})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@require_POST
def set_session_quorum_view(request, session_id):
    """Override the quorum mode for this session's remote-user gate.

    Accepts ``quorum`` in POST body (values: ``all`` or ``first_win``).
    Stores the override in Redis so the next run (after auto-continue) picks it up.
    """
    if not _has_valid_secret(request):
        return util.json_error("Unauthorized", 403)

    quorum = request.POST.get("quorum", "").strip()
    if quorum not in ("all", "first_win"):
        return util.json_error("Invalid quorum value. Expected 'all' or 'first_win'.", 400)

    try:
        set_session_quorum(session_id, quorum)
    except SessionCoordinationError as exc:
        logger.error(
            "agents.remote_user.quorum_set_error",
            extra={"session_id": session_id, "quorum": quorum, "error": str(exc)},
        )
        return util.json_error("Unable to update session quorum.", 503)

    return util.json_response({"ok": True, "quorum": quorum})


def _validate_token(token: str) -> dict | None:
    """Return token metadata dict or None if invalid/expired."""
    if not token or len(token) > 200:
        return None
    try:
        from agents.session_coordination import get_remote_user_token_data
        return get_remote_user_token_data(token)
    except SessionCoordinationError:
        return None


def _maybe_publish_complete(project_or_session, session_id: str) -> None:
    """Publish a 'complete' event when all required remote users are online/ignored.

    ``project_or_session`` may be a project dict (when we already have it) or
    a session dict (when we only have the session — project will be fetched).
    """
    try:
        # Resolve project from whichever was passed.
        if "project_id" in project_or_session and "agents" not in project_or_session:
            # Looks like a session dict — fetch the project.
            project = services.get_project(project_or_session["project_id"])
        else:
            project = project_or_session

        if project is None:
            return

        remote_users_cfg = (project.get("human_gate") or {}).get("remote_users") or []
        all_names = [r["name"] for r in remote_users_cfg if isinstance(r, dict) and r.get("name")]
        if not all_names:
            return

        statuses = get_remote_user_statuses(session_id, all_names)
        satisfied = all(
            statuses.get(name, "offline") in ("online", "ignored")
            for name in all_names
        )
        if satisfied:
            publish_remote_user_event(session_id, {"type": "complete"})
            logger.info(
                "agents.remote_user.all_ready",
                extra={"session_id": session_id},
            )
        else:
            # Publish a count update so the host WS panel reflects the change.
            online_count = sum(1 for s in statuses.values() if s == "online")
            ignored_count = sum(1 for s in statuses.values() if s == "ignored")
            required_count = len(all_names) - ignored_count
            publish_remote_user_event(session_id, {
                "type": "count_update",
                "online_count": online_count,
                "required_count": required_count,
            })
    except Exception:  # noqa: BLE001
        pass  # Pub/sub failure is non-fatal.
