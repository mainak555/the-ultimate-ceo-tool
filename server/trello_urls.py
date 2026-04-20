"""Trello integration URL routes."""

from django.urls import path
from . import trello_views

urlpatterns = [
    # Auth callback (no session_id — Trello redirects here)
    path("callback/", trello_views.trello_callback, name="trello_callback"),
    # Session-scoped token status (resolves project token via session's project_id)
    path("<str:session_id>/token-status/", trello_views.trello_token_status, name="trello_token_status"),
    # Session-scoped API proxies
    path("<str:session_id>/workspaces/", trello_views.trello_workspaces, name="trello_workspaces"),
    path("<str:session_id>/boards/", trello_views.trello_boards, name="trello_boards"),
    path("<str:session_id>/lists/", trello_views.trello_lists, name="trello_lists"),
    path("<str:session_id>/create-board/", trello_views.trello_create_board, name="trello_create_board"),
    path("<str:session_id>/create-list/", trello_views.trello_create_list, name="trello_create_list"),
    # Export
    path("<str:session_id>/extract/<str:discussion_id>/", trello_views.trello_extract, name="trello_extract"),
    path("<str:session_id>/export/<str:discussion_id>/", trello_views.trello_export_data, name="trello_export_data"),
    path("<str:session_id>/push/", trello_views.trello_push, name="trello_push"),
    # Project-scoped token management (config page)
    path("project/<str:project_id>/auth-url/", trello_views.trello_project_auth_url, name="trello_project_auth_url"),
    path("project/<str:project_id>/store-token/", trello_views.trello_project_store_token, name="trello_project_store_token"),
    path("project/<str:project_id>/token-status/", trello_views.trello_project_token_status, name="trello_project_token_status"),
    # Project-scoped API proxies (config page cascade dropdowns)
    path("project/<str:project_id>/workspaces/", trello_views.trello_project_workspaces, name="trello_project_workspaces"),
    path("project/<str:project_id>/boards/", trello_views.trello_project_boards, name="trello_project_boards"),
    path("project/<str:project_id>/lists/", trello_views.trello_project_lists, name="trello_project_lists"),
    path("project/<str:project_id>/create-board/", trello_views.trello_project_create_board, name="trello_project_create_board"),
    path("project/<str:project_id>/create-list/", trello_views.trello_project_create_list, name="trello_project_create_list"),
]
