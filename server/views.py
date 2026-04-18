"""
HTMX view controllers — thin layer between HTTP and business logic.

Each view:
  1. Parses request data
  2. Delegates to services.py
  3. Renders an HTMX partial (or full page for index)
"""

import asyncio
import json
from datetime import datetime, timezone

from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import services


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_valid_secret(request):
    """Check the secret key passed in the request headers."""
    key = request.headers.get("X-App-Secret-Key", "").strip()
    return services.verify_secret_key(key)


def _get_form_context(project=None, mode="create", success=None):
    """Return shared context for create and update forms."""
    context = {
        "project": project,
        "mode": mode,
        "model_names": services.get_available_models(),
        "default_system_prompt": services.get_system_prompt_template(),
        "selector_prompt_hint": services.get_selector_prompt_hint(),
        "trello_export_prompt_hint": services.get_trello_export_prompt_hint(),
    }
    if success:
        context["success"] = success
    return context


def _parse_form_agents(post_data):
    """
    Extract agent list from the flat POST form data.

    Form fields use bracket notation:
      agents[0][name], agents[0][model], agents[0][system_prompt], ...
    """
    agents = []
    idx = 0
    while f"agents[{idx}][name]" in post_data:
        prefix = f"agents[{idx}]"
        agents.append({
            "name": post_data.get(f"{prefix}[name]", "").strip(),
            "model": post_data.get(f"{prefix}[model]", "").strip(),
            "system_prompt": post_data.get(f"{prefix}[system_prompt]", "").strip(),
            "temperature": post_data.get(f"{prefix}[temperature]", "0.7").strip() or "0.7",
        })
        idx += 1

    return agents


def _build_project_data(post_data):
    """Build a project data dict from POST form fields."""
    human_gate_enabled = post_data.get("human_gate[enabled]") == "on"
    integrations_enabled = post_data.get("integrations[enabled]") == "on"
    trello_enabled = post_data.get("integrations[trello][enabled]") == "on"

    integrations = {
        "enabled": integrations_enabled,
        "export_agent": post_data.get("integrations[export_agent]", "").strip(),
        "trello": {
            "enabled": trello_enabled,
            "app_name": post_data.get("integrations[trello][app_name]", "").strip(),
            "api_key": post_data.get("integrations[trello][api_key]", "").strip(),
            "default_workspace": post_data.get("integrations[trello][default_workspace]", "").strip(),
            "default_board_name": post_data.get("integrations[trello][default_board_name]", "").strip(),
            "default_list_name": post_data.get("integrations[trello][default_list_name]", "").strip(),
            "export_mapping": {
                "system_prompt": post_data.get("integrations[trello][export_mapping][system_prompt]", "").strip(),
            },
        },
    }

    return {
        "project_name": post_data.get("project_name", "").strip(),
        "objective": post_data.get("objective", "").strip(),
        "agents": _parse_form_agents(post_data),
        "human_gate": {
            "enabled": human_gate_enabled,
            "name": post_data.get("human_gate[name]", "").strip(),
            "interaction_mode": post_data.get(
                "human_gate[interaction_mode]",
                "approve_reject",
            ).strip(),
        },
        "team": {
            "type": post_data.get("team[type]", "round_robin").strip(),
            "max_iterations": post_data.get("team[max_iterations]", "5").strip(),
            "model": post_data.get("team[model]", "").strip(),
            "system_prompt": post_data.get("team[system_prompt]", "").strip(),
            "temperature": post_data.get("team[temperature]", "0.0").strip() or "0.0",
            "allow_repeated_speaker": post_data.get("team[allow_repeated_speaker]"),
        },
        "integrations": integrations,
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _render_shell(request, projects=None, auto_open_create=False):
    """Render the full SPA shell. Passes projects so sidebar is server-rendered."""
    if projects is None:
        projects = services.list_projects()
    return render(request, "server/config.html", {
        "auto_open_create": auto_open_create,
        "projects": projects,
        "model_names": services.get_available_models(),
        "default_system_prompt": services.get_system_prompt_template(),
    })


@require_GET
def index(request):
    """Render the chat home page."""
    projects = services.list_projects()
    return render(request, "server/home.html", {"projects": projects})


@require_GET
def configurations_page(request):
    """Render the configurations workspace with create form preloaded."""
    return _render_shell(request, auto_open_create=True)


@require_GET
def project_list(request):
    """HTMX partial — sidebar list of all projects."""
    projects = services.list_projects()
    return render(request, "server/partials/sidebar.html", {
        "projects": projects,
    })


@require_GET
def project_new(request):
    """HTMX partial — blank configuration form for creating a new project."""
    return render(request, "server/partials/config_form.html", _get_form_context())


@require_POST
def project_create(request):
    """HTMX partial — create a new project from the form body."""
    if not _has_valid_secret(request):
        return HttpResponse(
            '<div class="alert alert-error">Unauthorized. Enter a valid Secret Key in the header before saving.</div>',
            status=403,
        )

    data = _build_project_data(request.POST)
    try:
        project = services.create_project(data)
    except ValueError as e:
        return HttpResponse(
            f'<div class="alert alert-error">{e}</div>',
            status=400,
        )

    response = render(
        request,
        "server/partials/config_form.html",
        _get_form_context(project=project, mode="update", success="Saved successfully!"),
    )
    response["HX-Trigger"] = "refreshSidebar"
    return response


@require_POST
def project_delete(request, project_id):
    """HTMX partial — delete a project (POST only, secret-key gated)."""
    if not _has_valid_secret(request):
        return HttpResponse(
            '<div class="alert alert-error">Unauthorized.</div>',
            status=403,
        )

    try:
        services.delete_project(project_id)
    except ValueError as e:
        return HttpResponse(
            f'<div class="alert alert-error">{e}</div>',
            status=404,
        )

    # Return empty string so hx-swap="outerHTML" removes the <li>,
    # then trigger a full sidebar refresh for consistency.
    response = HttpResponse("")
    response["HX-Trigger"] = "refreshSidebar"
    return response


@require_POST
def project_clone(request, project_id):
    """HTMX partial — clone a project as '{name} - Copy' (POST only, secret-key gated)."""
    if not _has_valid_secret(request):
        return HttpResponse(
            '<div class="alert alert-error">Unauthorized. Enter a valid Secret Key in the header before cloning.</div>',
            status=403,
        )

    try:
        project = services.clone_project(project_id)
    except ValueError as e:
        return HttpResponse(
            f'<div class="alert alert-error">{e}</div>',
            status=400,
        )

    response = render(
        request,
        "server/partials/config_form.html",
        _get_form_context(project=project, mode="update", success=f"Cloned as \u2018{project['project_name']}\u2019!"),
    )
    response["HX-Trigger"] = "refreshSidebar"
    return response


@require_http_methods(["GET", "POST"])
def project_detail(request, project_id):
    """
    GET  — Load project config (form if admin, readonly otherwise).
    POST — Create or update project config (admin only).
    """
    if request.method == "GET":
        project = services.get_project(project_id)
        if project is None:
            return HttpResponse(
                '<div class="alert alert-error">Project not found.</div>',
                status=404,
            )

        if _has_valid_secret(request):
            return render(
                request,
                "server/partials/config_form.html",
                _get_form_context(project=project, mode="update"),
            )

        else:
            return render(request, "server/partials/config_readonly.html", {
                "project": project,
            })

    # POST — update
    if not _has_valid_secret(request):
        return HttpResponse(
            '<div class="alert alert-error">Unauthorized. Enter a valid Secret Key in the header before saving.</div>',
            status=403,
        )

    data = _build_project_data(request.POST)

    try:
        project = services.update_project(project_id, data)
    except ValueError as e:
        return HttpResponse(
            f'<div class="alert alert-error">{e}</div>',
            status=400,
        )

    # Return the updated form + trigger sidebar refresh
    response = render(
        request,
        "server/partials/config_form.html",
        _get_form_context(project=project, mode="update", success="Saved successfully!"),
    )
    response["HX-Trigger"] = "refreshSidebar"
    return response


# ---------------------------------------------------------------------------
# Chat Session Views
# ---------------------------------------------------------------------------

@require_GET
def chat_session_list(request):
    """HTMX partial — list chat sessions for a given project."""
    project_id = request.GET.get("project_id", "").strip()
    sessions = services.list_chat_sessions(project_id) if project_id else []
    project = services.get_project(project_id) if project_id else None
    list_html = render_to_string(
        "server/partials/chat_session_list.html",
        {"sessions": sessions, "project_id": project_id},
        request=request,
    )
    context_html = render_to_string(
        "server/partials/chat_session_history.html",
        {"project": project},
        request=request,
    )
    oob_html = f'<div id="chat-messages" hx-swap-oob="innerHTML">{context_html}</div>'
    return HttpResponse(list_html + oob_html, content_type="text/html")


@require_POST
def chat_session_create(request):
    """HTMX — create a new chat session (secret-key gated)."""
    if not _has_valid_secret(request):
        # Error goes into #new-session-form-feedback (inside the modal).
        # Session list and messages panels are untouched.
        return HttpResponse(
            '<div class="alert alert-error">Unauthorized. Enter a valid Secret Key before creating a session.</div>',
            status=403,
        )

    project_id = request.POST.get("project_id", "").strip()
    description = request.POST.get("description", "").strip()
    try:
        session = services.create_chat_session(project_id, description)
    except ValueError as e:
        return HttpResponse(f'<div class="alert alert-error">{e}</div>', status=400)

    # On success the primary target (#new-session-form-feedback) gets empty
    # content (the modal closes via HX-Trigger). OOB swaps update the sidebar
    # list and the main messages panel.
    sessions = services.list_chat_sessions(project_id)
    list_html = render_to_string(
        "server/partials/chat_session_list.html",
        {"sessions": sessions, "project_id": project_id, "active_session_id": session["session_id"]},
        request=request,
    )
    history_html = render_to_string(
        "server/partials/chat_session_history.html",
        {"session": session},
        request=request,
    )
    oob_list = f'<div id="chat-history-list" hx-swap-oob="innerHTML">{list_html}</div>'
    oob_messages = f'<div id="chat-messages" hx-swap-oob="innerHTML">{history_html}</div>'
    # Set active-session-id directly in the DOM — more reliable than an xhr header
    sid = session["session_id"]
    oob_session_id = (
        f'<input id="active-session-id" hx-swap-oob="outerHTML" type="hidden" value="{sid}">'
    )
    # Primary content: empty (feedback div cleared, modal closes via trigger)
    response = HttpResponse(oob_list + oob_messages + oob_session_id, content_type="text/html")
    response["HX-Trigger"] = "chatSessionCreated"
    return response


@require_GET
def chat_session_detail(request, session_id):
    """HTMX partial — conversation history for a session (always readable)."""
    session = services.get_chat_session(session_id)
    if session is None:
        return HttpResponse(
            '<div class="alert alert-error">Session not found.</div>',
            status=404,
        )
    project = services.get_project(session["project_id"]) if session.get("project_id") else None
    return render(request, "server/partials/chat_session_history.html", {
        "session": session,
        "project": project,
    })


@require_POST
def chat_session_delete(request, session_id):
    """HTMX — delete a chat session (secret-key gated)."""
    if not _has_valid_secret(request):
        return HttpResponse(
            '<div class="alert alert-error">Unauthorized.</div>',
            status=403,
        )
    try:
        services.delete_chat_session(session_id)
    except ValueError as e:
        return HttpResponse(f'<div class="alert alert-error">{e}</div>', status=404)
    return HttpResponse("")


@require_POST
def chat_session_update(request, session_id):
    """HTMX — update a chat session's description (secret-key gated)."""
    if not _has_valid_secret(request):
        return HttpResponse(
            '<div class="alert alert-error">Unauthorized.</div>',
            status=403,
        )

    description = request.POST.get("description", "").strip()
    try:
        session = services.update_chat_session(session_id, description)
    except ValueError as e:
        return HttpResponse(f'<div class="alert alert-error">{e}</div>', status=400)

    # Re-render the session list so the sidebar reflects the updated description.
    project_id = session.get("project_id", "")
    sessions = services.list_chat_sessions(project_id)
    list_html = render_to_string(
        "server/partials/chat_session_list.html",
        {"sessions": sessions, "project_id": project_id, "active_session_id": session["session_id"]},
        request=request,
    )
    oob_list = f'<div id="chat-history-list" hx-swap-oob="innerHTML">{list_html}</div>'

    # Also update the history header description if user is viewing this session.
    oob_header = (
        f'<span class="chat-history-header__description" hx-swap-oob="innerHTML">'
        f'{session["description"]}</span>'
    )

    response = HttpResponse(oob_list + oob_header, content_type="text/html")
    response["HX-Trigger"] = "chatSessionUpdated"
    return response


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Agent execution — SSE streaming run
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
async def chat_session_run(request, session_id):
    """
    POST /chat/sessions/<id>/run/

    Start (or resume) an agent run for the given session.
    Returns a text/event-stream SSE response.

    Body fields:
      task  — the user message / feedback text (empty string = resume approve)
    """
    if not _has_valid_secret(request):
        return HttpResponse(json.dumps({"error": "Unauthorized"}), status=403,
                            content_type="application/json")

    session = await asyncio.to_thread(services.get_chat_session, session_id)
    if session is None:
        return HttpResponse(json.dumps({"error": "Session not found"}), status=404,
                            content_type="application/json")

    valid_states = ("idle", "awaiting_input")
    if session["status"] not in valid_states:
        return HttpResponse(
            json.dumps({"error": f"Session is currently '{session['status']}'"}),
            status=409, content_type="application/json",
        )

    project = await asyncio.to_thread(services.get_project, session["project_id"])
    if project is None:
        return HttpResponse(json.dumps({"error": "Project not found"}), status=404,
                            content_type="application/json")

    task = request.POST.get("task", "").strip()

    # First run must have a task; resume (approve) may send empty string
    is_first_run = session["status"] == "idle" and not session.get("discussion")
    if is_first_run and not task:
        return HttpResponse(
            json.dumps({"error": "'task' is required to start a conversation."}),
            status=400, content_type="application/json",
        )

    await asyncio.to_thread(services.set_session_status, session_id, "running")

    async def event_stream():
        from autogen_agentchat.base import TaskResult
        from autogen_agentchat.messages import TextMessage
        from agents.runtime import get_or_build_team, reset_cancel_token, evict_team

        team, _ = get_or_build_team(session_id, project)
        # Issue a fresh cancellation token for this run
        cancel_token = reset_cancel_token(session_id)

        has_gate = project.get("human_gate", {}).get("enabled", False)
        max_iter = project.get("team", {}).get("max_iterations", 5)

        # Export integration metadata for client-side export buttons
        integrations = project.get("integrations") or {}
        export_enabled = integrations.get("enabled", False)
        export_agent = integrations.get("export_agent", "")
        export_providers = []
        if export_enabled:
            trello_cfg = integrations.get("trello") or {}
            if trello_cfg.get("enabled"):
                export_providers.append({
                    "name": "trello",
                    "label": "Trello",
                    "default_board_name": trello_cfg.get("default_board_name", ""),
                    "default_list_name": trello_cfg.get("default_list_name", ""),
                })

        export_meta = {
            "enabled": export_enabled and len(export_providers) > 0,
            "export_agent": export_agent,
            "providers": export_providers,
        } if export_enabled else None

        pending_messages = []

        # Persist the human's message (initial task or gate feedback) to the discussion.
        if task:
            human_name = project.get("human_gate", {}).get("name") or "You"
            pending_messages.append({
                "agent_name": human_name,
                "role": "user",
                "content": task,
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M"),
            })

        try:
            async for msg in team.run_stream(
                task=task if task else None,
                cancellation_token=cancel_token,
            ):
                if isinstance(msg, TaskResult):
                    # Persist accumulated messages
                    if pending_messages:
                        await asyncio.to_thread(services.append_messages, session_id, pending_messages)
                        pending_messages = []

                    # Re-fetch to get current_round after potential $inc
                    updated = await asyncio.to_thread(services.get_chat_session, session_id)
                    current_round = updated["current_round"] if updated else 0

                    if has_gate and current_round < max_iter:
                        await asyncio.to_thread(services.set_session_status, session_id, "awaiting_input")
                        gate_data = {
                            "mode": project["human_gate"]["interaction_mode"],
                            "round": current_round + 1,
                            "max_rounds": max_iter,
                            "human_name": project["human_gate"]["name"],
                        }
                        if export_meta:
                            gate_data["export"] = export_meta
                        yield _sse("gate", gate_data)
                    else:
                        await asyncio.to_thread(services.set_session_status, session_id, "completed")
                        evict_team(session_id)
                        done_data = {"status": "completed", "round": current_round}
                        if export_meta:
                            done_data["export"] = export_meta
                        yield _sse("done", done_data)

                elif isinstance(msg, TextMessage) and msg.source != "user":
                    ts = datetime.now(timezone.utc).strftime("%H:%M")
                    record = {
                        "agent_name": msg.source,
                        "role": "assistant",
                        "content": msg.content,
                        "timestamp": ts,
                    }
                    pending_messages.append(record)
                    sse_record = dict(record)
                    # Attach export info for the client to decide button rendering
                    if export_meta:
                        sse_record["export"] = export_meta
                    yield _sse("message", sse_record)

        except asyncio.CancelledError:
            if pending_messages:
                await asyncio.to_thread(services.append_messages, session_id, pending_messages)
            await asyncio.to_thread(services.set_session_status, session_id, "stopped")
            evict_team(session_id)
            yield _sse("stopped", {"status": "stopped"})

        except Exception as exc:
            await asyncio.to_thread(services.set_session_status, session_id, "idle")
            evict_team(session_id)
            yield _sse("error", {"message": str(exc)})

        finally:
            # Guard: if session is still "running" (e.g. client disconnected mid-stream),
            # reset to "idle" so it can be re-run.
            stuck = await asyncio.to_thread(services.get_chat_session, session_id)
            if stuck and stuck["status"] == "running":
                await asyncio.to_thread(services.set_session_status, session_id, "idle")
                evict_team(session_id)

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ---------------------------------------------------------------------------
# Human-in-the-loop response
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def chat_session_respond(request, session_id):
    """
    POST /chat/sessions/<id>/respond/

    Human gate decision endpoint.

    Body:
      action — "approve" | "feedback" | "stop"
      text   — feedback text (only for action=feedback)
    """
    if not _has_valid_secret(request):
        return HttpResponse(json.dumps({"error": "Unauthorized"}), status=403,
                            content_type="application/json")

    session = services.get_chat_session(session_id)
    if session is None:
        return HttpResponse(json.dumps({"error": "Session not found"}), status=404,
                            content_type="application/json")

    if session["status"] != "awaiting_input":
        return HttpResponse(
            json.dumps({"error": f"Session is not awaiting input (status: {session['status']})"}),
            status=409, content_type="application/json",
        )

    action = request.POST.get("action", "").strip()
    text = request.POST.get("text", "").strip()

    if action == "stop":
        from agents.runtime import evict_team
        services.set_session_status(session_id, "stopped")
        evict_team(session_id)
        return HttpResponse(json.dumps({"status": "stopped"}), content_type="application/json")

    if action in ("approve", "feedback"):
        services.set_session_status(session_id, "idle")
        task = text if action == "feedback" else ""
        return HttpResponse(
            json.dumps({"status": "ok", "task": task}),
            content_type="application/json",
        )

    return HttpResponse(json.dumps({"error": "Invalid action"}), status=400,
                        content_type="application/json")


# ---------------------------------------------------------------------------
# Mid-run abort
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def chat_session_stop(request, session_id):
    """
    POST /chat/sessions/<id>/stop/

    Abort a currently-running session. Returns immediately; the SSE stream
    handles the CancelledError and emits a 'stopped' event.
    """
    if not _has_valid_secret(request):
        return HttpResponse(json.dumps({"error": "Unauthorized"}), status=403,
                            content_type="application/json")

    from agents.runtime import cancel_team
    cancel_team(session_id)
    return HttpResponse(json.dumps({"status": "cancelling"}), content_type="application/json")
