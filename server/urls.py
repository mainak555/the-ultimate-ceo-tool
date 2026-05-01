from django.urls import include, path
from . import views

app_name = "server"

urlpatterns = [
    # Full page
    path("", views.index, name="index"),

    # Full page — configurations workspace (sidebar + create form)
    path("projects/", views.configurations_page, name="configurations_page"),

    # HTMX partials — project list
    path("projects/list/", views.project_list, name="project_list"),

    # HTMX partials — new configuration form (blank)
    path("projects/new/", views.project_new, name="project_new"),

    # HTMX partial — create a new project (POST only)
    path("projects/create/", views.project_create, name="project_create"),

    # HTMX partial — delete a project (POST only)
    path("projects/<str:project_id>/delete/", views.project_delete, name="project_delete"),

    # HTMX partial — clone a project as '{name} - Copy' (POST only)
    path("projects/<str:project_id>/clone/", views.project_clone, name="project_clone"),

    # HTMX partials — single project (GET = detail, POST = update)
    path("projects/<str:project_id>/", views.project_detail, name="project_detail"),

    # Chat sessions
    path("chat/sessions/", views.chat_session_list, name="chat_session_list"),
    path("chat/sessions/create/", views.chat_session_create, name="chat_session_create"),
    path("chat/sessions/<str:session_id>/run/", views.chat_session_run, name="chat_session_run"),
    path("chat/sessions/<str:session_id>/restart/", views.chat_session_restart, name="chat_session_restart"),
    path("chat/sessions/<str:session_id>/respond/", views.chat_session_respond, name="chat_session_respond"),
    path("chat/sessions/<str:session_id>/attachments/", views.chat_session_upload_attachments, name="chat_session_upload_attachments"),
    path("chat/sessions/<str:session_id>/attachments/<str:attachment_id>/content/", views.chat_session_attachment_content, name="chat_session_attachment_content"),
    path("chat/sessions/<str:session_id>/stop/", views.chat_session_stop, name="chat_session_stop"),
    path("chat/sessions/<str:session_id>/readiness/status/", views.chat_session_readiness_status, name="chat_session_readiness_status"),
    path("chat/sessions/<str:session_id>/readiness/check/", views.chat_session_readiness_check, name="chat_session_readiness_check"),
    path("chat/sessions/<str:session_id>/readiness/<str:user_id>/token/", views.chat_session_readiness_token, name="chat_session_readiness_token"),
    path("chat/sessions/<str:session_id>/remote/heartbeat/", views.chat_session_remote_heartbeat, name="chat_session_remote_heartbeat"),
    path("chat/sessions/<str:session_id>/remote/attachments/", views.chat_session_remote_upload_attachments, name="chat_session_remote_upload_attachments"),
    path("chat/sessions/<str:session_id>/delete/", views.chat_session_delete, name="chat_session_delete"),
    path("chat/sessions/<str:session_id>/update/", views.chat_session_update, name="chat_session_update"),
    path("chat/sessions/<str:session_id>/", views.chat_session_detail, name="chat_session_detail"),
    path("chat/<str:session_id>/remote-user/<str:token>/", views.remote_user_page, name="remote_user_page"),

    # Trello integration
    path("trello/", include("server.trello_urls")),

    # Jira integration
    path("jira/", include("server.jira_urls")),

    # MCP OAuth 2.0
    path("mcp/", include("server.mcp_urls")),
]
