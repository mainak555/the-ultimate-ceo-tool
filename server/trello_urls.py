"""Trello integration URL routes."""

from django.urls import path
from . import trello_views

urlpatterns = [
    # Auth callback (no session_id — Trello redirects here)
    path("callback/", trello_views.trello_callback, name="trello_callback"),
    # Token management
    path("<str:session_id>/auth-url/", trello_views.trello_auth_url, name="trello_auth_url"),
    path("<str:session_id>/store-token/", trello_views.trello_store_token, name="trello_store_token"),
    path("<str:session_id>/token-status/", trello_views.trello_token_status, name="trello_token_status"),
    # API proxies
    path("<str:session_id>/workspaces/", trello_views.trello_workspaces, name="trello_workspaces"),
    path("<str:session_id>/boards/", trello_views.trello_boards, name="trello_boards"),
    path("<str:session_id>/lists/", trello_views.trello_lists, name="trello_lists"),
    path("<str:session_id>/create-board/", trello_views.trello_create_board, name="trello_create_board"),
    path("<str:session_id>/create-list/", trello_views.trello_create_list, name="trello_create_list"),
    # Export
    path("<str:session_id>/extract/", trello_views.trello_extract, name="trello_extract"),
    path("<str:session_id>/push/", trello_views.trello_push, name="trello_push"),
]
