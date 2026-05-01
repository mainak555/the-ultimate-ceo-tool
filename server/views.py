"""
HTMX view controllers — thin layer between HTTP and business logic.

Each view:
  1. Parses request data
  2. Delegates to services.py
  3. Renders an HTMX partial (or full page for index)
"""

import asyncio
import io
import json
import logging
from urllib.parse import quote
from datetime import datetime, timezone
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import services
from . import attachment_service
from .logging_utils import bind_request_id, clear_request_id, get_request_id
from core.tracing import context_from_traceparent, start_root_span


logger = logging.getLogger(__name__)


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
            "quorum": post_data.get("human_gate[quorum]", "yes").strip() or "yes",
            "remote_users": _parse_remote_users(post_data),
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
        "mcp_oauth_configs": _parse_mcp_oauth_configs(post_data),
    }


def _parse_mcp_oauth_configs(post_data):
    """
    Extract MCP OAuth configs dict from POST form fields.

    Form fields: mcp_oauth_configs[N][server_name], [auth_url], [token_url],
                 [client_id], [client_secret], [scopes]
    Returns {server_name: {auth_url, token_url, client_id, client_secret, scopes}}
    Skips rows with empty server_name.
    """
    configs = {}
    idx = 0
    while any(
        f"mcp_oauth_configs[{idx}][{field}]" in post_data
        for field in ("server_name", "auth_url", "token_url", "client_id", "client_secret")
    ):
        server_name = post_data.get(f"mcp_oauth_configs[{idx}][server_name]", "").strip()
        if server_name:
            configs[server_name] = {
                "auth_url":      post_data.get(f"mcp_oauth_configs[{idx}][auth_url]", "").strip(),
                "token_url":     post_data.get(f"mcp_oauth_configs[{idx}][token_url]", "").strip(),
                "client_id":     post_data.get(f"mcp_oauth_configs[{idx}][client_id]", "").strip(),
                "client_secret": post_data.get(f"mcp_oauth_configs[{idx}][client_secret]", ""),
                "scopes":        post_data.get(f"mcp_oauth_configs[{idx}][scopes]", "").strip(),
            }
        idx += 1
    return configs


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


def _parse_remote_users(post_data):
    """
    Extract Human Gate remote_users list from POST form fields.

    Form fields: human_gate[remote_users][N][name], [description]
    Skips rows with empty name (blank rows are ignored). The `id` is derived
    server-side in `validate_human_gate` from `name` (slug). Any submitted
    `[id]` field is ignored.
    """
    rows = []
    idx = 0
    prefix = "human_gate[remote_users]"
    while any(
        f"{prefix}[{idx}][{field}]" in post_data
        for field in ("id", "name", "description")
    ):
        name = post_data.get(f"{prefix}[{idx}][name]", "").strip()
        if name:
            rows.append({
                "name": name,
                "description": post_data.get(f"{prefix}[{idx}][description]", "").strip(),
            })
        idx += 1
    return rows


def _normalize_export_agents(raw_agents):
    """Return a clean list of export agent names."""
    if isinstance(raw_agents, str):
        raw_agents = [raw_agents] if raw_agents else []
    if not isinstance(raw_agents, list):
        return []
    return [name.strip() for name in raw_agents if isinstance(name, str) and name.strip()]


def _parse_attachment_ids(post_data):
    """Return de-duplicated attachment IDs from form POST data."""
    values = []
    values.extend(post_data.getlist("attachment_ids"))
    values.extend(post_data.getlist("attachment_ids[]"))
    clean = []
    seen = set()
    for raw in values:
        aid = (raw or "").strip()
        if not aid or aid in seen:
            continue
        clean.append(aid)
        seen.add(aid)
    return clean


_ATTACHMENT_ICON_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "csv", "txt", "json", "xml", "md",
}


def _enrich_attachments_for_display(session_id, attachments):
    """Attach session-scoped URLs for attachment previews/downloads."""
    out = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        aid = (row.get("id") or "").strip()
        if not aid:
            continue
        url = f"/chat/sessions/{session_id}/attachments/{aid}/content/"
        row["content_url"] = url
        if row.get("is_image"):
            row["thumbnail_url"] = url
        else:
            ext = (row.get("extension") or "").lower()
            icon = ext if ext in _ATTACHMENT_ICON_EXTENSIONS else "document"
            row["thumbnail_url"] = f"/static/server/assets/icons/file-{icon}.svg"
        out.append(row)
    return out


def _build_agent_task_for_run(task_text: str, session_id: str, attachment_ids):
    """Return the task to pass to ``team.run_stream``.

    * No attachments → return ``task_text`` unchanged (plain ``str``).
    * Images present → download bytes, wrap as ``autogen_core.Image`` objects,
      and return a ``MultiModalMessage`` whose ``content`` list is
      ``[task_text, img1, img2, ...]``.  This gives vision-capable models
      actual pixel data.
    * Non-image attachments are already incorporated into ``task_text`` via
      the ``---\\nAttachments:`` block produced by
      ``build_attachment_context_block``.
    """
    if not attachment_ids:
        return task_text

    try:
        images = attachment_service.load_images_for_agents(
            session_id=session_id, attachment_ids=attachment_ids
        )
    except Exception:
        logger.exception("views.load_images_failed", extra={"session_id": session_id})
        images = []

    if not images:
        return task_text

    # Build MultiModalMessage — lazy import to avoid cost at module load.
    try:
        import PIL.Image
        from autogen_core import Image as AutoGenImage
        from autogen_agentchat.messages import MultiModalMessage
    except Exception:
        logger.warning(
            "views.multimodal_import_failed",
            extra={"session_id": session_id},
        )
        return task_text

    content: list = [task_text]
    for filename, raw, _mime in images:
        try:
            pil_img = PIL.Image.open(io.BytesIO(raw))
            content.append(AutoGenImage(pil_img))
        except Exception:
            logger.warning(
                "views.image_decode_failed",
                extra={"session_id": session_id, "filename": filename},
            )

    if len(content) == 1:
        # All image loads failed — fall back to plain text.
        return task_text

    return MultiModalMessage(content=content, source="user")


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
    session_id = session.get("session_id", "") if isinstance(session, dict) else ""
    is_gate = bool(isinstance(session, dict) and session.get("status") == "awaiting_input")
    last_user_index = -1
    for msg in (session.get("discussions") if isinstance(session, dict) else []) or []:
        row = dict(msg)
        row["attachments"] = _enrich_attachments_for_display(session_id, row.get("attachments") or [])
        row["is_current_turn"] = False
        if row.get("role") != "user":
            if row.get("id"):
                row["visible_export_providers"] = _filter_export_providers(
                    export_meta,
                    row.get("agent_name", ""),
                )
            else:
                row["visible_export_providers"] = []
        else:
            last_user_index = len(history_messages)
        history_messages.append(row)
    if is_gate and last_user_index >= 0 and last_user_index < len(history_messages):
        history_messages[last_user_index]["is_current_turn"] = True
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

def _json_default(value):
    """JSON serializer fallback for datetime payload values."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps(payload) -> str:
    """Serialize JSON payloads with datetime support."""
    return json.dumps(payload, default=_json_default)

def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {_json_dumps(data)}\n\n"


def _json_error(message: str, status: int) -> HttpResponse:
    """Return a standard JSON error response."""
    return HttpResponse(_json_dumps({"error": message}), status=status, content_type="application/json")


def _remote_group_name(session_id: str) -> str:
    return f"remote_session_{session_id}"


def _broadcast_remote_state(session_id: str) -> None:
    if not session_id:
        return
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(_remote_group_name(session_id), {"type": "remote.state"})
    except Exception:
        logger.debug("remote.websocket.broadcast_failed", extra={"session_id": session_id})


async def _abroadcast_remote_state(session_id: str) -> None:
    if not session_id:
        return
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        await layer.group_send(_remote_group_name(session_id), {"type": "remote.state"})
    except Exception:
        logger.debug("remote.websocket.broadcast_failed", extra={"session_id": session_id})


def _resolve_remote_user_context(request, session_id, token: str = ""):
    """Resolve and validate a remote user join token for a chat session."""
    resolved_token = (
        (token or "").strip()
        or (request.headers.get("X-Remote-User-Token", "") or "").strip()
        or (request.GET.get("token", "") or "").strip()
        or (request.POST.get("token", "") or "").strip()
    )
    if not resolved_token:
        return None

    from agents.session_coordination import lookup_remote_user_token, set_remote_user_online

    user_id = lookup_remote_user_token(session_id, resolved_token)
    if not user_id:
        return None

    session = services.get_chat_session(session_id)
    if session is None:
        return None
    project = services.get_project(session.get("project_id", ""))
    if project is None:
        return None

    remote_user = services.get_remote_user(project, user_id)
    if not remote_user:
        return None

    set_remote_user_online(session_id, user_id)
    return {
        "token": resolved_token,
        "user_id": user_id,
        "remote_user": remote_user,
        "session": session,
        "project": project,
    }


def _friendly_run_error(exc: Exception) -> str:
    """Return a user-readable error string for agent run failures.

    AutoGen's BaseGroupChat wraps agent exceptions as::

        raise RuntimeError(str(message.error))

    so the outer ``exc`` is always a ``RuntimeError`` whose ``str()`` begins
    with the original exception class name, e.g.::

        "BadRequestError: Error code: 400 - {'error': {...}}\nTraceback:..."

    We unwrap the inner ``BadRequestError`` (direct OR wrapped in RuntimeError)
    so the chat UI shows an actionable message rather than a raw JSON blob.
    """
    import json as _json

    # Helper: parse a BadRequestError-like object into a friendly string.
    def _format_bad_request(err_obj) -> str:
        try:
            body = getattr(err_obj, "body", None) or {}
            inner = body.get("error", {}) if isinstance(body, dict) else {}
            code = inner.get("code") or ""
            api_msg = inner.get("message") or str(err_obj)
            # Azure wraps the Anthropic error as a JSON string inside 'message'.
            if isinstance(api_msg, str) and api_msg.startswith("{"):
                try:
                    api_msg = _json.loads(api_msg).get("error", {}).get("message") or api_msg
                except Exception:  # noqa: BLE001
                    pass
            return (
                f"Model API error ({code}): {api_msg}. "
                "The model could not complete the tool-call reflection step. "
                "Start a new session to continue (the session state has been reset)."
            )
        except Exception:  # noqa: BLE001
            return str(err_obj)

    # 1. Direct BadRequestError (openai package).
    try:
        from openai import BadRequestError as _OAIBadRequest
        if isinstance(exc, _OAIBadRequest):
            return _format_bad_request(exc)
    except ImportError:
        pass

    # 2. RuntimeError wrapping a BadRequestError — AutoGen's run_stream raises:
    #      raise RuntimeError(str(message.error))
    #    str(exc) begins with "BadRequestError: Error code: 400 - ..."
    exc_str = str(exc)
    if isinstance(exc, RuntimeError) and "BadRequestError" in exc_str and "invalid_prompt" in exc_str:
        # Extract the JSON body from the string representation.
        # Pattern: "Error code: 400 - {...}"
        import re as _re
        match = _re.search(r"Error code: \d+ - (\{.*)", exc_str, _re.DOTALL)
        if match:
            try:
                body = _json.loads(match.group(1).split("\nTraceback")[0])
                inner = body.get("error", {})
                code = inner.get("code") or "invalid_prompt"
                api_msg = inner.get("message") or "invalid prompt"
                if isinstance(api_msg, str) and api_msg.startswith("{"):
                    try:
                        api_msg = _json.loads(api_msg).get("error", {}).get("message") or api_msg
                    except Exception:  # noqa: BLE001
                        pass
                return (
                    f"Model API error ({code}): {api_msg}. "
                    "The model could not complete the tool-call reflection step. "
                    "Start a new session to continue (the session state has been reset)."
                )
            except Exception:  # noqa: BLE001
                pass
        return (
            "The model rejected the tool-call reflection prompt (invalid_prompt). "
            "Start a new session to continue (the session state has been reset)."
        )

    return exc_str


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
            attachment_ids — optional repeated form field values
    """
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    session = await asyncio.to_thread(services.get_chat_session, session_id)
    if session is None:
        return _json_error("Session not found", 404)

    valid_states = ("idle", "awaiting_input", "awaiting_oauth", "awaiting_remote_users")
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
    attachment_ids = _parse_attachment_ids(request.POST)

    # First run must have a task; gate resume may send empty string
    is_first_run = session["status"] == "idle" and not session.get("discussions")
    if is_first_run and not task and not attachment_ids:
        return _json_error("'task' is required to start a conversation.", 400)

    # Single-assistant chat mode: empty Continue is invalid (no new context for agent).
    # Attachments alone are not sufficient — a text message is required.
    is_single_assistant_gate = (
        project.get("human_gate", {}).get("enabled", False)
        and len(project.get("agents") or []) == 1
    )
    if is_single_assistant_gate and not is_first_run and not task:
        return _json_error("A message is required to continue.", 400)

    # Multi-user Human Gate — remote-user readiness pre-run gate.
    # Runs BEFORE the MCP OAuth gate so the leader can resolve human-presence
    # before triggering any external authorization. Does not acquire the lease.
    pending_remote = await asyncio.to_thread(
        services.compute_pending_remote_users, project, session_id
    )
    if pending_remote:
        await asyncio.to_thread(
            services.set_session_awaiting_remote_users, session_id, pending_remote
        )
        logger.info(
            "agents.session.awaiting_remote_users",
            extra={
                "session_id": session_id,
                "pending_count": len(pending_remote),
            },
        )
        return JsonResponse(
            {"status": "awaiting_remote_users", "users": pending_remote},
            status=409,
        )

    # MCP OAuth pre-run gate: any reachable MCP server that requires OAuth and
    # has no session-scoped Bearer token in Redis blocks the run start. The
    # session is parked in ``awaiting_oauth`` and the frontend renders the
    # in-history authorization card. The lease is NOT acquired here.
    pending_oauth = await asyncio.to_thread(
        services.compute_pending_oauth_servers, raw_project, session_id
    )
    if pending_oauth:
        await asyncio.to_thread(
            services.set_session_awaiting_oauth, session_id, pending_oauth
        )
        logger.info(
            "agents.mcp.oauth_gate_blocked",
            extra={
                "session_id": session_id,
                "server_count": len(pending_oauth),
                "server_names": pending_oauth,
            },
        )
        return JsonResponse(
            {"status": "awaiting_oauth", "servers": pending_oauth},
            status=409,
        )

    from agents.session_coordination import (
        SessionCoordinationError,
        acquire_run_lease,
        clear_cancel_signal,
        ensure_redis_available,
        get_heartbeat_interval_seconds,
        get_instance_id,
        is_cancel_signaled,
        release_run_lease,
        renew_run_lease,
    )

    owner_id = get_instance_id()

    redis_ok = await asyncio.to_thread(ensure_redis_available)
    if not redis_ok:
        return _json_error("Active session coordinator is unavailable.", 503)

    try:
        acquired = await asyncio.to_thread(acquire_run_lease, session_id, owner_id)
    except SessionCoordinationError:
        logger.exception(
            "agents.session.redis_unavailable",
            extra={"session_id": session_id, "phase": "acquire_lease"},
        )
        return _json_error("Active session coordinator is unavailable.", 503)

    if not acquired:
        return _json_error("Session is already running on another worker.", 409)

    try:
        await asyncio.to_thread(clear_cancel_signal, session_id)
        moved_to_running = await asyncio.to_thread(services.try_set_session_running, session_id)
        if not moved_to_running:
            try:
                await asyncio.to_thread(release_run_lease, session_id, owner_id)
            except SessionCoordinationError:
                logger.exception(
                    "agents.session.redis_unavailable",
                    extra={"session_id": session_id, "phase": "release_conflict"},
                )
            return _json_error("Session status changed before run start.", 409)
    except SessionCoordinationError:
        await asyncio.to_thread(release_run_lease, session_id, owner_id)
        logger.exception(
            "agents.session.redis_unavailable",
            extra={"session_id": session_id, "phase": "prepare_run"},
        )
        return _json_error("Active session coordinator is unavailable.", 503)

    await _abroadcast_remote_state(session_id)

    # Capture request_id now — middleware clears it before event_stream() runs.
    _captured_request_id = get_request_id()

    # Create a fresh root OTel span for this run.  Empty context → no parent →
    # fresh trace_id every round, so each /run/ call is independently queryable.
    # Store the W3C traceparent in Redis so event_stream() can reattach it after
    # the Django middleware finally-block clears the request span context.
    from agents.session_coordination import (
        clear_run_traceparent,
        store_run_traceparent,
    )
    _run_span, _run_traceparent = start_root_span(
        "agents.session.run", {"session_id": session_id}
    )
    if _run_traceparent:
        await asyncio.to_thread(store_run_traceparent, session_id, _run_traceparent)

    async def event_stream():
        nonlocal task
        # Re-bind request_id (cleared by middleware before the body is consumed).
        _rid_token = bind_request_id(_captured_request_id)
        # Reattach the run's root OTel span as the active span so every
        # agents.* log line and every @traced_function span inherits the same
        # trace_id.  context_from_traceparent reconstructs the parent context
        # from the stored Redis value; set_span_in_context then makes the
        # recording span current so child spans nest under it.
        _otel_parent_token = None
        _otel_span_token = None
        if _run_span is not None and _run_traceparent:
            try:
                from opentelemetry import context as otel_context, trace
                _parent_ctx = context_from_traceparent(_run_traceparent)
                if _parent_ctx is not None:
                    _otel_parent_token = otel_context.attach(_parent_ctx)
                _otel_span_token = otel_context.attach(
                    trace.set_span_in_context(_run_span)
                )
            except Exception:  # noqa: BLE001
                pass
        from autogen_agentchat.base import TaskResult
        from autogen_agentchat.messages import TextMessage, ToolCallSummaryMessage
        from agents.runtime import (
            evict_team,
            get_or_build_team,
            load_team_state,
            reset_cancel_token,
            save_team_state,
        )

        heartbeat_stop = asyncio.Event()
        heartbeat_task = None
        lease_lost = False

        async def _lease_heartbeat(cancel_token):
            nonlocal lease_lost
            interval_s = get_heartbeat_interval_seconds()
            while True:
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=interval_s)
                    return
                except asyncio.TimeoutError:
                    pass

                try:
                    renewed = await asyncio.to_thread(renew_run_lease, session_id, owner_id)
                except SessionCoordinationError:
                    lease_lost = True
                    cancel_token.cancel()
                    return

                if not renewed:
                    lease_lost = True
                    cancel_token.cancel()
                    return

        try:
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
                evict_team(session_id)
                # If the failure is a missing/expired MCP OAuth token,
                # re-park the session in awaiting_oauth and surface the same
                # in-history authorization card via an SSE event so the
                # frontend swap path is identical to the pre-run gate.
                pending_oauth_mid = await asyncio.to_thread(
                    services.compute_pending_oauth_servers, raw_project, session_id
                )
                if pending_oauth_mid:
                    await asyncio.to_thread(
                        services.set_session_awaiting_oauth, session_id, pending_oauth_mid
                    )
                    logger.info(
                        "agents.mcp.oauth_gate_blocked_midrun",
                        extra={
                            "session_id": session_id,
                            "server_count": len(pending_oauth_mid),
                            "server_names": pending_oauth_mid,
                        },
                    )
                    yield _sse("awaiting_oauth", {"servers": pending_oauth_mid})
                    return
                await asyncio.to_thread(services.set_session_status, session_id, "idle")
                yield _sse("error", {"message": str(exc)})
                return

            # Issue a fresh cancellation token for this run
            cancel_token = reset_cancel_token(session_id)

            has_gate = project.get("human_gate", {}).get("enabled", False)
            max_iter = project.get("team", {}).get("max_iterations", 5)
            is_single_assistant_chat_mode = (
                has_gate and len(project.get("agents") or []) == 1
            )

            # Export integration metadata for client-side export actions.
            export_meta = _build_export_meta(project)

            pending_messages = []

            async def checkpoint_state() -> None:
                state = await save_team_state(team)
                try:
                    await asyncio.to_thread(services.save_agent_state, session_id, state)
                except ValueError as _exc:
                    # State exceeds the MongoDB document-size budget (typically
                    # because image bytes are embedded in the AutoGen message
                    # history).  Log a warning and continue — the current run
                    # completes normally; only session resume will be unavailable.
                    logger.warning(
                        "agents.session.state_too_large",
                        extra={"session_id": session_id, "error": str(_exc)},
                    )

            # Persist the human's message (initial task or gate notes) to discussions.
            if task or attachment_ids:
                human_name = project.get("human_gate", {}).get("name") or "You"
                human_message_id = str(uuid4())
                attachments = await asyncio.to_thread(
                    attachment_service.bind_attachments_to_message,
                    session_id=session_id,
                    message_id=human_message_id,
                    attachment_ids=attachment_ids,
                )
                text_with_context = task + await asyncio.to_thread(
                    attachment_service.build_attachment_context_block,
                    session_id=session_id,
                    attachment_ids=attachment_ids,
                )
                # Build the actual task for the agent: plain str or MultiModalMessage
                # when vision images are attached.
                task_for_agent = await asyncio.to_thread(
                    _build_agent_task_for_run,
                    text_with_context,
                    session_id,
                    attachment_ids,
                )
                attachments_for_display = _enrich_attachments_for_display(session_id, attachments)
                pending_messages.append({
                    "id": human_message_id,
                    "agent_name": human_name,
                    "role": "user",
                    # Store only the user's raw typed text — not the attachment
                    # context block.  Extracted attachment text is an ephemeral
                    # runtime artefact built from Blob → Redis on each run; it
                    # must not be persisted in discussions[].
                    "content": task,
                    "attachments": attachments_for_display,
                    "timestamp": datetime.now(timezone.utc),  # BSON Date in MongoDB
                })
                # Persist the human turn immediately so remote viewers get live history.
                await asyncio.to_thread(services.append_messages, session_id, pending_messages)
                await _abroadcast_remote_state(session_id)
                pending_messages = []
                task = task_for_agent
            else:
                task_for_agent = task

            heartbeat_task = asyncio.create_task(_lease_heartbeat(cancel_token))

            # Claude 4+ (and future Anthropic models) reject conversations whose
            # last message is an AssistantMessage — this is the "prefill" pattern
            # that Anthropic removed.  When a human-gate resume carries no text
            # (task_for_agent is falsy), AutoGen calls run_stream(task=None) which
            # adds no new UserMessage, leaving each agent's model context ending
            # with its own prior AssistantMessage.  The fix is to inject a minimal
            # synthetic user turn so the model context always ends with a user
            # message.  The synthetic string is NOT persisted to discussions[]
            # (pending_messages is only built when task or attachment_ids are
            # present, and both are falsy in this branch) and is NOT shown in the
            # chat UI (the SSE loop only emits TextMessage where source!="user").
            effective_task: str | None = task_for_agent if task_for_agent else None
            if effective_task is None and not is_first_run:
                effective_task = "Continue."

            try:
                async for msg in team.run_stream(
                    task=effective_task,
                    cancellation_token=cancel_token,
                ):
                    try:
                        if await asyncio.to_thread(is_cancel_signaled, session_id):
                            cancel_token.cancel()
                    except SessionCoordinationError:
                        lease_lost = True
                        cancel_token.cancel()

                    if isinstance(msg, TaskResult):
                        # Persist accumulated messages
                        if pending_messages:
                            await asyncio.to_thread(services.append_messages, session_id, pending_messages)
                            pending_messages = []

                        await checkpoint_state()

                        # Re-fetch to get current_round after potential $inc
                        updated = await asyncio.to_thread(services.get_chat_session, session_id)
                        current_round = updated["current_round"] if updated else 0

                        if has_gate and (
                            is_single_assistant_chat_mode or current_round < max_iter
                        ):
                            await asyncio.to_thread(services.set_session_status, session_id, "awaiting_input")
                            await _abroadcast_remote_state(session_id)
                            gate_session = await asyncio.to_thread(services.get_chat_session, session_id)
                            if gate_session and (project.get("human_gate") or {}).get("remote_users"):
                                from agents.session_coordination import initialize_remote_gate_round

                                turn_state = await asyncio.to_thread(
                                    services.compute_remote_turn_state,
                                    project,
                                    gate_session,
                                    "",
                                )
                                await asyncio.to_thread(
                                    initialize_remote_gate_round,
                                    session_id,
                                    int(gate_session.get("current_round") or 0),
                                    turn_state.get("required_user_ids") or [],
                                )
                            gate_data = {
                                "round": current_round + 1,
                                "max_rounds": None if is_single_assistant_chat_mode else max_iter,
                                "human_name": project["human_gate"]["name"],
                                "chat_mode": "single_assistant" if is_single_assistant_chat_mode else "team",
                            }
                            if export_meta:
                                gate_data["export"] = export_meta
                            yield _sse("gate", gate_data)
                        else:
                            await asyncio.to_thread(services.set_session_status, session_id, "completed")
                            await _abroadcast_remote_state(session_id)
                            evict_team(session_id)
                            done_data = {"status": "completed", "round": current_round}
                            if export_meta:
                                done_data["export"] = export_meta
                            yield _sse("done", done_data)

                    elif isinstance(msg, TextMessage) and msg.source != "user":
                        ts_dt = datetime.now(timezone.utc)  # BSON Date for MongoDB
                        ts_iso = ts_dt.isoformat()           # ISO string for SSE JSON
                        record = {
                            "id": str(uuid4()),
                            "agent_name": msg.source,
                            "role": "assistant",
                            "content": msg.content,
                            "timestamp": ts_dt,
                        }
                        await asyncio.to_thread(services.append_messages, session_id, [record])
                        await _abroadcast_remote_state(session_id)
                        sse_record = dict(record)
                        sse_record["timestamp"] = ts_iso
                        # Attach export info for the client to decide button rendering
                        if export_meta:
                            sse_record["export"] = export_meta
                        yield _sse("message", sse_record)

                    elif isinstance(msg, ToolCallSummaryMessage) and msg.source != "user":
                        # Emitted when reflect_on_tool_use is False or unavailable.
                        # Persist and stream so tool results are visible in the chat
                        # even without a full reflection LLM call.
                        ts_dt = datetime.now(timezone.utc)
                        ts_iso = ts_dt.isoformat()
                        record = {
                            "id": str(uuid4()),
                            "agent_name": msg.source,
                            "role": "assistant",
                            "content": msg.content,
                            "timestamp": ts_dt,
                        }
                        await asyncio.to_thread(services.append_messages, session_id, [record])
                        await _abroadcast_remote_state(session_id)
                        sse_record = dict(record)
                        sse_record["timestamp"] = ts_iso
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
                if lease_lost:
                    yield _sse("error", {"message": "Run lease lost; session stopped."})
                else:
                    yield _sse("stopped", {"status": "stopped"})

            except Exception as exc:
                logger.exception(
                    "agents.session.run_error",
                    extra={"session_id": session_id, "exc_type": type(exc).__name__},
                )
                # Flush any pending messages (user task + partial assistant turns)
                # so the discussion thread reflects what actually happened before
                # the failure. Persistence failures here must not mask the
                # original error, so they are swallowed with a log line.
                if pending_messages:
                    try:
                        await asyncio.to_thread(services.append_messages, session_id, pending_messages)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "agents.session.append_messages_failed_on_error",
                            extra={"session_id": session_id, "pending": len(pending_messages)},
                        )
                    pending_messages = []
                try:
                    await checkpoint_state()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "agents.session.checkpoint_failed_on_error",
                        extra={"session_id": session_id},
                    )
                await asyncio.to_thread(services.set_session_status, session_id, "idle")
                evict_team(session_id)
                # AutoGen's BaseGroupChat wraps exceptions as:
                #   raise RuntimeError(str(message.error))
                # so exc is always RuntimeError whose str() begins with the
                # original exception class name (e.g. "BadRequestError: ...").
                # We check both the direct type and the string representation.
                user_msg = _friendly_run_error(exc)
                yield _sse("error", {"message": user_msg})

            finally:
                heartbeat_stop.set()
                if heartbeat_task:
                    try:
                        await heartbeat_task
                    except Exception:  # noqa: BLE001
                        pass

                # Guard: if session is still "running" (e.g. client disconnected mid-stream),
                # reset to "idle" so it can be re-run.
                stuck = await asyncio.to_thread(services.get_chat_session, session_id)
                if stuck and stuck["status"] == "running":
                    await asyncio.to_thread(services.set_session_status, session_id, "idle")
                    evict_team(session_id)
        finally:
            try:
                await asyncio.to_thread(release_run_lease, session_id, owner_id)
            except SessionCoordinationError:
                logger.exception(
                    "agents.session.redis_unavailable",
                    extra={"session_id": session_id, "phase": "release_lease"},
                )
            try:
                await asyncio.to_thread(clear_cancel_signal, session_id)
            except SessionCoordinationError:
                logger.exception(
                    "agents.session.redis_unavailable",
                    extra={"session_id": session_id, "phase": "clear_cancel"},
                )
            # Detach OTel contexts (reverse order), end the root span, clear
            # the Redis traceparent key, and restore request_id.
            if _otel_span_token is not None:
                try:
                    from opentelemetry import context as otel_context
                    otel_context.detach(_otel_span_token)
                except Exception:  # noqa: BLE001
                    pass
            if _otel_parent_token is not None:
                try:
                    from opentelemetry import context as otel_context
                    otel_context.detach(_otel_parent_token)
                except Exception:  # noqa: BLE001
                    pass
            if _run_span is not None:
                try:
                    _run_span.end()
                except Exception:  # noqa: BLE001
                    pass
            try:
                await asyncio.to_thread(clear_run_traceparent, session_id)
            except Exception:  # noqa: BLE001
                pass
            # Restore request_id ContextVar to pre-generator default.
            clear_request_id(_rid_token)

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

    project = services.get_project(session.get("project_id", "")) if session.get("project_id") else None

    action = request.POST.get("action", "").strip()
    text = request.POST.get("text", "").strip()
    attachment_ids = _parse_attachment_ids(request.POST)

    if action == "stop":
        from agents.runtime import evict_team
        services.set_session_status(session_id, "stopped")
        evict_team(session_id)
        _broadcast_remote_state(session_id)
        return HttpResponse(_json_dumps({"status": "stopped"}), content_type="application/json")

    if action == "continue":
        remote_text, remote_attachment_ids = services.pop_remote_gate_resume_payload(
            project,
            session_id,
            int(session.get("current_round") or 0),
        )
        merged_task = text
        if remote_text:
            merged_task = (merged_task + remote_text) if merged_task else remote_text.lstrip("\n")
        merged_attachment_ids = []
        seen = set()
        for aid in list(attachment_ids or []) + list(remote_attachment_ids or []):
            aid_str = str(aid or "").strip()
            if not aid_str or aid_str in seen:
                continue
            merged_attachment_ids.append(aid_str)
            seen.add(aid_str)
        services.set_session_status(session_id, "idle")
        _broadcast_remote_state(session_id)
        return HttpResponse(
            _json_dumps({"status": "ok", "task": merged_task, "attachment_ids": merged_attachment_ids}),
            content_type="application/json",
        )

    return HttpResponse(_json_dumps({"error": "Invalid action"}), status=400,
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
    return HttpResponse(_json_dumps({"status": "ok", "task": task, "mode": mode}), content_type="application/json")


# ---------------------------------------------------------------------------
# Multi-user Human Gate — remote-user readiness endpoints (Phase 2)
# ---------------------------------------------------------------------------

@require_GET
def chat_session_readiness_status(request, session_id):
    """
    GET /chat/sessions/<id>/readiness/status/

    Return the readiness snapshot for the leader's pre-run lobby.
    Shape: ``{"users": [{"user_id", "name", "description", "online", "checked", "has_token"}]}``.
    """
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)
    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)
    project = services.get_project(session["project_id"])
    if project is None:
        return _json_error("Project not found", 404)
    base_url = request.build_absolute_uri("/").rstrip("/")
    return HttpResponse(
        _json_dumps(services.get_remote_users_status(project, session_id, base_url=base_url)),
        content_type="application/json",
    )


@csrf_exempt
@require_POST
def chat_session_readiness_check(request, session_id):
    """
    POST /chat/sessions/<id>/readiness/check/

    Body: ``user_ids`` (repeated form field) — the leader's checked-set.
    Validates each ID against the configured remote-user list and persists in Redis.
    """
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)
    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)
    project = services.get_project(session["project_id"])
    if project is None:
        return _json_error("Project not found", 404)

    configured_ids = {
        str(u.get("id") or "").strip()
        for u in (project.get("human_gate") or {}).get("remote_users") or []
        if isinstance(u, dict) and u.get("id")
    }
    submitted = [str(uid).strip() for uid in request.POST.getlist("user_ids") if uid]
    invalid = [uid for uid in submitted if uid not in configured_ids]
    if invalid:
        return _json_error("Unknown remote user id(s): " + ", ".join(invalid), 400)

    from agents.session_coordination import SessionCoordinationError, ensure_redis_available, set_checked_remote_users

    if not ensure_redis_available():
        return _json_error("Active session coordinator is unavailable.", 503)
    try:
        set_checked_remote_users(session_id, submitted)
    except SessionCoordinationError:
        return _json_error("Unable to persist checked-set.", 503)
    return HttpResponse(
        _json_dumps({"status": "ok", "checked": submitted}),
        content_type="application/json",
    )


@csrf_exempt
@require_POST
def chat_session_readiness_token(request, session_id, user_id):
    """
    POST /chat/sessions/<id>/readiness/<user_id>/token/

    Mint (or rotate) a join-URL token for a remote user. Returns ``{token, join_url}``.
    Tokens are URL-safe random strings, never logged.
    """
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)
    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)
    project = services.get_project(session["project_id"])
    if project is None:
        return _json_error("Project not found", 404)

    configured_ids = {
        str(u.get("id") or "").strip()
        for u in (project.get("human_gate") or {}).get("remote_users") or []
        if isinstance(u, dict) and u.get("id")
    }
    if user_id not in configured_ids:
        return _json_error("Unknown remote user id.", 400)

    from agents.session_coordination import SessionCoordinationError, ensure_redis_available, get_or_mint_remote_user_token

    if not ensure_redis_available():
        return _json_error("Active session coordinator is unavailable.", 503)
    try:
        token = get_or_mint_remote_user_token(session_id, user_id)
    except SessionCoordinationError:
        return _json_error("Unable to mint token.", 503)

    base_url = (
        request.build_absolute_uri("/").rstrip("/")
    )
    # Join URL for the remote participant page.
    join_url = f"{base_url}/chat/{session_id}/remote-user/{token}/"
    return HttpResponse(
        _json_dumps({"status": "ok", "join_url": join_url}),
        content_type="application/json",
    )


@require_GET
def remote_user_page(request, session_id, token):
    """Render the remote participant chat page for a session invitation URL."""
    ctx = _resolve_remote_user_context(request, session_id, token)
    if not ctx:
        return HttpResponse("<div class='alert alert-error'>Invitation link is invalid or expired.</div>", status=403)

    session = ctx["session"]
    project = ctx["project"]
    remote_user = ctx["remote_user"]
    export_meta = _build_export_meta(project)
    turn_state = services.compute_remote_turn_state(project, session, remote_user["user_id"])

    from agents.session_coordination import (
        get_or_mint_remote_export_capability,
        get_remote_user_heartbeat_interval_seconds,
    )

    capability = get_or_mint_remote_export_capability(session_id, remote_user["user_id"])

    participants = [
        {"name": "Leader", "online": True, "active": bool(session.get("status") == "awaiting_input")}
    ]
    for row in turn_state.get("participants") or []:
        if row.get("user_id") == remote_user["user_id"]:
            continue
        participants.append({
            "name": row.get("name") or row.get("user_id") or "User",
            "online": bool(row.get("online")),
            "active": bool(row.get("active")),
        })

    history_html = render_to_string(
        "server/partials/chat_session_history.html",
        {
            "session": session,
            "project": project,
            "history_export_meta": export_meta,
            "history_messages": _build_history_messages(session, export_meta),
            "history_viewer_name": ctx["remote_user"].get("name") or "",
        },
        request=request,
    )
    return render(
        request,
        "server/remote_user.html",
        {
            "session": session,
            "project": project,
            "remote_user": remote_user,
            "remote_token": token,
            "remote_export_capability": capability,
            "heartbeat_interval_seconds": get_remote_user_heartbeat_interval_seconds(),
            "can_send": bool(turn_state.get("can_send")),
            "participants": participants,
            "history_html": history_html,
        },
    )


@csrf_exempt
@require_POST
def chat_session_remote_heartbeat(request, session_id):
    """Refresh remote participant online presence TTL for a session."""
    ctx = _resolve_remote_user_context(request, session_id)
    if not ctx:
        return _json_error("Unauthorized", 403)
    from agents.session_coordination import set_remote_user_online

    set_remote_user_online(session_id, ctx["user_id"])
    return HttpResponse(_json_dumps({"status": "ok"}), content_type="application/json")


@csrf_exempt
@require_POST
def chat_session_remote_upload_attachments(request, session_id):
    """Upload one or more files for a remote participant during awaiting_input."""
    ctx = _resolve_remote_user_context(request, session_id)
    if not ctx:
        return _json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)
    if session.get("status") != "awaiting_input":
        return _json_error("Session is not awaiting input.", 409)

    files = list(request.FILES.getlist("files"))
    try:
        uploaded = attachment_service.upload_session_attachments(session=session, files=files)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception:
        logger.exception("attachments.remote_upload_failed", extra={"session_id": session_id, "user_id": ctx["user_id"]})
        return _json_error("Attachment upload failed.", 500)

    enriched = _enrich_attachments_for_display(session_id, uploaded)
    return HttpResponse(_json_dumps({"status": "ok", "attachments": enriched}), content_type="application/json")


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

    # If the session is parked at a pre-run gate (awaiting_remote_users /
    # awaiting_oauth) there is no SSE stream to catch CancelledError, so flip
    # the status back to idle directly. The frontend treats this as "cancelled".
    session = services.get_chat_session(session_id)
    if session and session.get("status") in ("awaiting_remote_users", "awaiting_oauth"):
        services.set_session_status(session_id, "idle")
        _broadcast_remote_state(session_id)

    from agents.session_coordination import SessionCoordinationError, ensure_redis_available, signal_cancel

    if not ensure_redis_available():
        return _json_error("Active session coordinator is unavailable.", 503)

    try:
        signal_cancel(session_id)
    except SessionCoordinationError:
        logger.exception(
            "agents.session.redis_unavailable",
            extra={"session_id": session_id, "phase": "signal_cancel"},
        )
        return _json_error("Active session coordinator is unavailable.", 503)

    from agents.runtime import cancel_team
    cancel_team(session_id)
    return HttpResponse(_json_dumps({"status": "cancelling"}), content_type="application/json")


@csrf_exempt
@require_POST
def chat_session_upload_attachments(request, session_id):
    """Upload one or more files for a session and return attachment descriptors."""
    if not _has_valid_secret(request):
        return _json_error("Unauthorized", 403)

    session = services.get_chat_session(session_id)
    if session is None:
        return _json_error("Session not found", 404)

    files = list(request.FILES.getlist("files"))
    try:
        uploaded = attachment_service.upload_session_attachments(session=session, files=files)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception:
        logger.exception("attachments.upload_failed", extra={"session_id": session_id})
        return _json_error("Attachment upload failed.", 500)

    enriched = _enrich_attachments_for_display(session_id, uploaded)
    return HttpResponse(_json_dumps({"status": "ok", "attachments": enriched}), content_type="application/json")


@require_GET
def chat_session_attachment_content(request, session_id, attachment_id):
    """Return raw attachment bytes for inline image thumbnails and download links."""
    session = services.get_chat_session(session_id)
    if session is None:
        return HttpResponse("Session not found.", status=404)

    try:
        raw, mime_type, filename = attachment_service.get_attachment_content(
            session_id=session_id,
            attachment_id=attachment_id,
        )
    except ValueError:
        return HttpResponse("Attachment not found.", status=404)
    except Exception:
        logger.exception(
            "attachments.content_failed",
            extra={"session_id": session_id, "attachment_id": attachment_id},
        )
        return HttpResponse("Attachment retrieval failed.", status=500)

    response = FileResponse(
        io.BytesIO(raw),
        content_type=(mime_type or "application/octet-stream"),
    )
    response["Content-Disposition"] = f"inline; filename=\"{quote(filename)}\""
    return response
