"""Standalone guest readonly invitation and join views."""

from __future__ import annotations

import logging

from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from agents.session_coordination import (
    SessionCoordinationError,
    generate_guest_token,
    get_guest_token_data,
    revoke_guest_token,
)

from . import services, util

logger = logging.getLogger(__name__)


def _has_valid_secret(request) -> bool:
    key = request.headers.get("X-App-Secret-Key", "").strip()
    return services.verify_secret_key(key)


@require_POST
def generate_guest_invite_link(request, session_id):
    """Generate (or replace) a guest readonly invite token for a chat session."""
    if not _has_valid_secret(request):
        return util.json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return util.json_error("Session not found", 404)

    project = services.get_project(session.get("project_id", ""))
    if project is None:
        return util.json_error("Project not found", 404)

    try:
        token = generate_guest_token(session_id, str(session["project_id"]))
    except SessionCoordinationError as exc:
        logger.error(
            "agents.guest.invite_error",
            extra={"session_id": session_id, "error": str(exc)},
        )
        return util.json_error("Unable to generate guest invitation link.", 503)

    join_url = request.build_absolute_uri(f"/guest/join/{token}/")
    logger.info("agents.guest.invite_generated", extra={"session_id": session_id})
    return util.json_response({
        "join_url": join_url,
        "session_id": session_id,
        "session_title": session.get("description") or "Session",
    })


@require_POST
def revoke_guest_invite_link(request, session_id):
    """Revoke guest invite token for a chat session and evict active guests."""
    if not _has_valid_secret(request):
        return util.json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return util.json_error("Session not found", 404)

    try:
        revoke_guest_token(session_id)
    except SessionCoordinationError as exc:
        logger.error(
            "agents.guest.revoke_error",
            extra={"session_id": session_id, "error": str(exc)},
        )
        return util.json_error("Unable to revoke guest invitation link.", 503)

    logger.info("agents.guest.invite_revoked", extra={"session_id": session_id})
    return util.json_response({"status": "revoked", "session_id": session_id})


@require_GET
def guest_join(request, token):
    """Render the standalone guest readonly page (public token-gated)."""
    token_data = _validate_guest_token(token)
    if token_data is None:
        return render(
            request,
            "server/guest_user.html",
            {"error": "This guest link has expired or is invalid."},
        )

    session_id = token_data["session_id"]
    project_id = token_data["project_id"]

    session = services.get_chat_session(session_id)
    if session is None:
        return render(
            request,
            "server/guest_user.html",
            {"error": "Session not found."},
        )

    project = services.get_project(project_id)
    project_name = project.get("project_name", "") if project else ""

    discussions = [
        d for d in (session.get("discussions") or [])
        if isinstance(d, dict)
    ]

    return render(request, "server/guest_user.html", {
        "token": token,
        "session_id": session_id,
        "session_title": session.get("description") or "Session",
        "project_name": project_name,
        "discussions": discussions,
        "session_status": str(session.get("status") or "idle"),
        "error": None,
    })


def _validate_guest_token(token: str) -> dict | None:
    """Return token metadata dict or None if invalid/expired."""
    if not token or len(token) > 200:
        return None
    try:
        return get_guest_token_data(token)
    except SessionCoordinationError:
        return None
