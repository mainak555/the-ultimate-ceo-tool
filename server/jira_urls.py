"""Jira integration URL routes."""

from django.urls import path
from . import jira_views

urlpatterns = [
    # -----------------------------------------------------------------------
    # Project-scoped routes (config page)
    # -----------------------------------------------------------------------
    # Verify credentials for a specific Jira type
    path(
        "project/<str:project_id>/verify/<str:type_name>/",
        jira_views.jira_project_verify,
        name="jira_project_verify",
    ),
    # List Jira project spaces for a type (config page cascade)
    path(
        "project/<str:project_id>/spaces/<str:type_name>/",
        jira_views.jira_project_spaces,
        name="jira_project_spaces",
    ),

    # -----------------------------------------------------------------------
    # Session-scoped routes (export modal)
    # -----------------------------------------------------------------------
    # Check if a Jira type is configured for this session's project
    path(
        "<str:session_id>/token-status/<str:type_name>/",
        jira_views.jira_session_status,
        name="jira_session_status",
    ),
    # List Jira project spaces for the export modal
    path(
        "<str:session_id>/spaces/<str:type_name>/",
        jira_views.jira_session_spaces,
        name="jira_session_spaces",
    ),
    # Fetch project-scoped metadata (issue types, priorities, sprints, epics)
    path(
        "<str:session_id>/metadata/<str:type_name>/",
        jira_views.jira_session_metadata,
        name="jira_session_metadata",
    ),
    # Extract issues from a discussion message
    path(
        "<str:session_id>/extract/<str:discussion_id>/<str:type_name>/",
        jira_views.jira_extract,
        name="jira_extract",
    ),
    # Load / save export payload (GET = load, POST = save)
    path(
        "<str:session_id>/export/<str:discussion_id>/<str:type_name>/",
        jira_views.jira_export_data,
        name="jira_export_data",
    ),
    # Raw markdown reference from discussion.content (shared across types)
    path(
        "<str:session_id>/reference/<str:discussion_id>/",
        jira_views.jira_reference,
        name="jira_reference",
    ),
    # Push issues to Jira
    path(
        "<str:session_id>/push/<str:type_name>/",
        jira_views.jira_push,
        name="jira_push",
    ),
]
