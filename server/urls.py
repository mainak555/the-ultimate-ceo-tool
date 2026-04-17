from django.urls import path
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

    # HTMX partials — single project (GET = detail, POST = update)
    path("projects/<str:project_id>/", views.project_detail, name="project_detail"),

    # Chat sessions
    path("chat/sessions/", views.chat_session_list, name="chat_session_list"),
    path("chat/sessions/create/", views.chat_session_create, name="chat_session_create"),
    path("chat/sessions/<str:session_id>/delete/", views.chat_session_delete, name="chat_session_delete"),
    path("chat/sessions/<str:session_id>/", views.chat_session_detail, name="chat_session_detail"),

]
