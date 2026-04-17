"""
HTMX view controllers — thin layer between HTTP and business logic.

Each view:
  1. Parses request data
  2. Delegates to services.py
  3. Renders an HTMX partial (or full page for index)
"""

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
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
        },
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _render_shell(request, projects=None, auto_open_create=False):
    """Render the full SPA shell. Passes projects so sidebar is server-rendered."""
    if projects is None:
        projects = services.list_projects()
    return render(request, "server/base.html", {
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

_SESSION_SELECT_WELCOME = (
    '<div class="chat-welcome">'
    '<div class="chat-welcome__icon">💬</div>'
    '<h2 class="chat-welcome__title">Select a session</h2>'
    '<p class="chat-welcome__subtitle">Choose an existing session from the list or start a new one with ＋.</p>'
    '</div>'
)


@require_GET
def chat_session_list(request):
    """HTMX partial — list chat sessions for a given project."""
    project_id = request.GET.get("project_id", "").strip()
    sessions = services.list_chat_sessions(project_id) if project_id else []
    list_html = render_to_string(
        "server/partials/chat_session_list.html",
        {"sessions": sessions, "project_id": project_id},
        request=request,
    )
    oob_html = f'<div id="chat-messages" hx-swap-oob="innerHTML">{_SESSION_SELECT_WELCOME}</div>'
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
    # Primary content: empty (feedback div cleared, modal closes via trigger)
    response = HttpResponse(oob_list + oob_messages, content_type="text/html")
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
    return render(request, "server/partials/chat_session_history.html", {"session": session})


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
