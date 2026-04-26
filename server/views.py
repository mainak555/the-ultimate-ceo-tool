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
from uuid import uuid4

from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import services


SUPPORTED_EXPORT_PROVIDERS = ("trello", "jira", "pdf", "n8n")
EXPORT_PROVIDER_LABELS = {
    "trello": "Trello",
    "jira": "Jira",
    "jira_software": "Jira Software",
    "jira_service_desk": "Jira Service Desk",
    "jira_business": "Jira Business",
    "pdf": "PDF",
    "n8n": "n8n",
}


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
        "jira_export_prompt_hints": {
            "software": services.get_jira_export_prompt_hint("software"),
            "service_desk": services.get_jira_export_prompt_hint("service_desk"),
            "business": services.get_jira_export_prompt_hint("business"),
        },
    }
    if success:
        context["success"] = success
    return context


def _parse_form_agents(post_data):
    """
    Extract agent list from the flat POST form data.

    Form fields use bracket notation:
      agents[0][name], agents[0][model], agents[0][system_prompt],
      agents[0][temperature], agents[0][mcp_tools], agents[0][mcp_configuration], ...
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
            "mcp_tools": (post_data.get(f"{prefix}[mcp_tools]", "none") or "none").strip().lower(),
            "mcp_configuration": post_data.get(f"{prefix}[mcp_configuration]", ""),
        })
        idx += 1

    return agents


def _build_project_data(post_data, existing_project=None):
    """Build a project data dict from POST form fields."""
    human_gate_enabled = post_data.get("human_gate[enabled]") == "on"
    integrations_enabled = post_data.get("integrations[enabled]") == "on"
    trello_enabled = post_data.get("integrations[trello][enabled]") == "on"

    integrations = {
        "enabled": integrations_enabled,
        "trello": {
            "enabled": trello_enabled,
            "export_agents": [n.strip() for n in post_data.getlist("integrations[trello][export_agents]") if n.strip()],
            "app_name": post_data.get("integrations[trello][app_name]", "").strip(),
            "api_key": post_data.get("integrations[trello][api_key]", "").strip(),
            "token": post_data.get("integrations[trello][token]", "").strip(),
            "token_generated_at": post_data.get("integrations[trello][token_generated_at]", "").strip(),
            "default_workspace_id": post_data.get("integrations[trello][default_workspace_id]", "").strip(),
            "default_workspace_name": post_data.get("integrations[trello][default_workspace_name]", "").strip(),
            "default_board_id": post_data.get("integrations[trello][default_board_id]", "").strip(),
            "default_board_name": post_data.get("integrations[trello][default_board_name]", "").strip(),
            "default_list_id": post_data.get("integrations[trello][default_list_id]", "").strip(),
            "default_list_name": post_data.get("integrations[trello][default_list_name]", "").strip(),
            "export_mapping": {
                "system_prompt": post_data.get("integrations[trello][export_mapping][system_prompt]", "").strip(),
                "model": post_data.get("integrations[trello][export_mapping][model]", "").strip(),
                "temperature": post_data.get("integrations[trello][export_mapping][temperature]", "0.0").strip(),
            },
        },
    }

    # --- Jira ---
    jira_enabled = post_data.get("integrations[jira][enabled]") == "on"
    jira = {"enabled": jira_enabled}
    for jira_type in ("software", "service_desk", "business"):
        pfx = f"integrations[jira][{jira_type}]"
        type_enabled = post_data.get(f"{pfx}[enabled]") == "on"
        jira[jira_type] = {
            "enabled": type_enabled,
            "site_url": post_data.get(f"{pfx}[site_url]", "").strip(),
            "email": post_data.get(f"{pfx}[email]", "").strip(),
            "api_key": post_data.get(f"{pfx}[api_key]", "").strip(),
            "default_project_key": post_data.get(f"{pfx}[default_project_key]", "").strip(),
            "default_project_name": post_data.get(f"{pfx}[default_project_name]", "").strip(),
            "export_agents": [n.strip() for n in post_data.getlist(f"{pfx}[export_agents]") if n.strip()],
            "export_mapping": {
                "system_prompt": post_data.get(f"{pfx}[export_mapping][system_prompt]", "").strip(),
                "model": post_data.get(f"{pfx}[export_mapping][model]", "").strip(),
                "temperature": post_data.get(f"{pfx}[export_mapping][temperature]", "0.0").strip(),
            },
        }
    integrations["jira"] = jira

    if isinstance(existing_project, dict):
        existing_integrations = existing_project.get("integrations") or {}
        for provider_name in SUPPORTED_EXPORT_PROVIDERS:
            if provider_name in ("trello", "jira"):
                continue
            provider_cfg = existing_integrations.get(provider_name)
            if isinstance(provider_cfg, dict):
                integrations[provider_name] = dict(provider_cfg)

    return {
        "project_name": post_data.get("project_name", "").strip(),
        "objective": post_data.get("objective", "").strip(),
        "agents": _parse_form_agents(post_data),
        "human_gate": {
            "enabled": human_gate_enabled,
            "name": post_data.get("human_gate[name]", "").strip(),
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
        "shared_mcp_tools": post_data.get("shared_mcp_tools", ""),
        "mcp_secrets": _parse_mcp_secrets(post_data),
    }


def _parse_mcp_secrets(post_data):
    """
    Extract MCP secrets dict from POST form fields.

    Form fields: mcp_secrets[N][key], mcp_secrets[N][value]
    Returns {KEY: value}. Skips rows with empty key.
    """
    secrets = {}
    idx = 0
    while (
        f"mcp_secrets[{idx}][key]" in post_data
        or f"mcp_secrets[{idx}][value]" in post_data
    ):
        key = post_data.get(f"mcp_secrets[{idx}][key]", "").strip()
        value = post_data.get(f"mcp_secrets[{idx}][value]", "")
        if key:
            secrets[key] = value
        idx += 1
    return secrets


def _normalize_export_agents(raw_agents):
    """Return a clean list of export agent names."""
    if isinstance(raw_agents, str):
        raw_agents = [raw_agents] if raw_agents else []
    if not isinstance(raw_agents, list):
        return []
    return [name.strip() for name in raw_agents if isinstance(name, str) and name.strip()]


def _build_export_meta(project):
    """Build provider metadata for export actions from project integrations."""
    integrations = project.get("integrations") if isinstance(project, dict) else {}
    if not isinstance(integrations, dict) or not integrations.get("enabled", False):
        return None

    providers = []
    for provider_name in SUPPORTED_EXPORT_PROVIDERS:
        provider_cfg = integrations.get(provider_name)
        if not isinstance(provider_cfg, dict) or not provider_cfg.get("enabled", False):
            continue

        if provider_name == "jira":
            # Emit one entry per enabled Jira sub-type, each with its own export_agents
            for jira_type in ("software", "service_desk", "business"):
                type_cfg = provider_cfg.get(jira_type) or {}
                if not type_cfg.get("enabled", False):
                    continue
                sub_key = f"jira_{jira_type}"
                providers.append({
                    "name": sub_key,
                    "label": EXPORT_PROVIDER_LABELS.get(sub_key, sub_key.replace("_", " ").title()),
                    "export_agents": _normalize_export_agents(type_cfg.get("export_agents")),
                })
            continue

        providers.append({
            "name": provider_name,
            "label": EXPORT_PROVIDER_LABELS.get(provider_name, provider_name.title()),
            "export_agents": _normalize_export_agents(provider_cfg.get("export_agents")),
        })

    if not providers:
        return None

    return {
        "enabled": True,
        "providers": providers,
    }


def _filter_export_providers(export_meta, agent_name):
    """Return export providers visible for a given agent name."""
    if not export_meta or not export_meta.get("enabled"):
        return []

    target = (agent_name or "").strip().lower()
    visible = []
    for provider in export_meta.get("providers") or []:
        allowlist = provider.get("export_agents") or []
        if not allowlist:
            visible.append(provider)
            continue
        if any((name or "").strip().lower() == target for name in allowlist):
            visible.append(provider)
    return visible


def _build_history_messages(session, export_meta):
    """Attach visible export providers to assistant messages for history rendering."""
    history_messages = []
    for msg in (session.get("discussions") if isinstance(session, dict) else []) or []:
        row = dict(msg)
        if row.get("role") != "user":
            if row.get("id"):
                row["visible_export_providers"] = _filter_export_providers(
                    export_meta,
                    row.get("agent_name", ""),
                )
            else:
                row["visible_export_providers"] = []
        history_messages.append(row)
    return history_messages


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
        "selector_prompt_hint": services.get_selector_prompt_hint(),
        "trello_export_prompt_hint": services.get_trello_export_prompt_hint(),
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
    except services.ProjectDeletionBlocked as e:
        return HttpResponse(
            f'<div class="alert alert-error">{e}</div>',
            status=400,
        )
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

    existing_project = services.get_project(project_id)
    data = _build_project_data(request.POST, existing_project=existing_project)

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
    export_meta = _build_export_meta(project)
    list_html = render_to_string(
        "server/partials/chat_session_list.html",
        {"sessions": sessions, "project_id": project_id},
        request=request,
    )
    context_html = render_to_string(
        "server/partials/chat_session_history.html",
        {"project": project, "history_export_meta": export_meta},
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
    project = services.get_project(project_id)
    export_meta = _build_export_meta(project)
    list_html = render_to_string(
        "server/partials/chat_session_list.html",
        {"sessions": sessions, "project_id": project_id, "active_session_id": session["session_id"]},
        request=request,
    )
    history_html = render_to_string(
        "server/partials/chat_session_history.html",
        {
            "session": session,
            "project": project,
            "history_export_meta": export_meta,
            "history_messages": _build_history_messages(session, export_meta),
        },
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
    export_meta = _build_export_meta(project)
    return render(request, "server/partials/chat_session_history.html", {
        "session": session,
        "project": project,
        "history_export_meta": export_meta,
        "history_messages": _build_history_messages(session, export_meta),
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


def _json_error(message: str, status: int) -> HttpResponse:
    """Return a standard JSON error response."""
    return HttpResponse(json.dumps({"error": message}), status=status, content_type="application/json")


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
            task  — the user message / optional gate notes (empty string = resume)
    """
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    session = await asyncio.to_thread(services.get_chat_session, session_id)
    if session is None:
        return _json_error("Session not found", 404)

    valid_states = ("idle", "awaiting_input")
    if session["status"] not in valid_states:
        return _json_error(f"Session is currently '{session['status']}'", 409)

    project = await asyncio.to_thread(services.get_project, session["project_id"])
    if project is None:
        return _json_error("Project not found", 404)

    # Runtime needs unmasked MCP secrets for placeholder substitution; the
    # normalized project carries SECRET_MASK values for UI safety.
    raw_project = await asyncio.to_thread(services.get_project_raw, session["project_id"])
    if isinstance(raw_project, dict):
        project["mcp_secrets"] = raw_project.get("mcp_secrets") or {}

    task = request.POST.get("task", "").strip()

    # First run must have a task; gate resume may send empty string
    is_first_run = session["status"] == "idle" and not session.get("discussions")
    if is_first_run and not task:
        return _json_error("'task' is required to start a conversation.", 400)

    await asyncio.to_thread(services.set_session_status, session_id, "running")

    async def event_stream():
        from autogen_agentchat.base import TaskResult
        from autogen_agentchat.messages import TextMessage
        from agents.runtime import (
            evict_team,
            get_or_build_team,
            load_team_state,
            reset_cancel_token,
            save_team_state,
        )

        try:
            team, _, cache_miss = get_or_build_team(session_id, project)
            if cache_miss:
                saved_state = await asyncio.to_thread(services.get_agent_state, session_id)
                if saved_state:
                    try:
                        await load_team_state(team, saved_state)
                    except Exception:
                        evict_team(session_id)
                        await asyncio.to_thread(services.set_session_status, session_id, "stopped")
                        yield _sse("error", {"message": "Unable to restart: state version mismatch."})
                        return
        except Exception as exc:
            await asyncio.to_thread(services.set_session_status, session_id, "idle")
            evict_team(session_id)
            yield _sse("error", {"message": str(exc)})
            return

        # Issue a fresh cancellation token for this run
        cancel_token = reset_cancel_token(session_id)

        has_gate = project.get("human_gate", {}).get("enabled", False)
        max_iter = project.get("team", {}).get("max_iterations", 5)

        # Export integration metadata for client-side export actions.
        export_meta = _build_export_meta(project)

        pending_messages = []

        async def checkpoint_state() -> None:
            state = await save_team_state(team)
            await asyncio.to_thread(services.save_agent_state, session_id, state)

        # Persist the human's message (initial task or gate notes) to discussions.
        if task:
            human_name = project.get("human_gate", {}).get("name") or "You"
            pending_messages.append({
                "id": str(uuid4()),
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

                    await checkpoint_state()

                    # Re-fetch to get current_round after potential $inc
                    updated = await asyncio.to_thread(services.get_chat_session, session_id)
                    current_round = updated["current_round"] if updated else 0

                    if has_gate and current_round < max_iter:
                        await asyncio.to_thread(services.set_session_status, session_id, "awaiting_input")
                        gate_data = {
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
                        "id": str(uuid4()),
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
            try:
                await checkpoint_state()
            except Exception:
                # Stop should still succeed even if persistence fails here.
                pass
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
            action — "continue" | "stop"
            text   — optional user context to inject before continuing
    """
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)

    if session["status"] != "awaiting_input":
        return _json_error(f"Session is not awaiting input (status: {session['status']})", 409)

    action = request.POST.get("action", "").strip()
    text = request.POST.get("text", "").strip()

    if action == "stop":
        from agents.runtime import evict_team
        services.set_session_status(session_id, "stopped")
        evict_team(session_id)
        return HttpResponse(json.dumps({"status": "stopped"}), content_type="application/json")

    if action == "continue":
        services.set_session_status(session_id, "idle")
        return HttpResponse(
            json.dumps({"status": "ok", "task": text}),
            content_type="application/json",
        )

    return HttpResponse(json.dumps({"error": "Invalid action"}), status=400,
                        content_type="application/json")


@csrf_exempt
@require_POST
def chat_session_restart(request, session_id):
    """
    POST /chat/sessions/<id>/restart/

    Restart a completed/stopped session from persisted AutoGen state.

    Body:
      mode - "continue_only" | "continue_with_context"
      text - optional instruction when mode=continue_with_context
    """
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)

    if session.get("status") not in ("completed", "stopped"):
        return _json_error(f"Session cannot be restarted from status '{session.get('status')}'.", 409)

    if not session.get("has_agent_state"):
        return _json_error("No persisted agent state is available for this session.", 409)

    mode = (request.POST.get("mode", "continue_only") or "continue_only").strip()
    text = (request.POST.get("text", "") or "").strip()
    if mode not in ("continue_only", "continue_with_context"):
        return _json_error("Invalid restart mode.", 400)

    if mode == "continue_with_context" and not text:
        return _json_error("'text' is required when mode is continue_with_context.", 400)

    services.set_session_status(session_id, "idle")
    task = text if mode == "continue_with_context" else ""
    return HttpResponse(json.dumps({"status": "ok", "task": task, "mode": mode}), content_type="application/json")


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
        return _json_error("Unauthorized", 403)

    from agents.runtime import cancel_team
    cancel_team(session_id)
    return HttpResponse(json.dumps({"status": "cancelling"}), content_type="application/json")
