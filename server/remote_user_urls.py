"""Remote-user URL routes."""

from django.urls import path
from . import remote_user_views

# Host-facing routes — included under chat/sessions/ in server/urls.py
urlpatterns = [
    # Generate an invite link for a remote user
    path(
        "<str:session_id>/remote-users/<str:user_name>/invite/",
        remote_user_views.generate_invite_link,
        name="remote_user_invite",
    ),
    # Ignore (un-check) a remote user for this run
    path(
        "<str:session_id>/remote-users/<str:user_name>/ignore/",
        remote_user_views.ignore_remote_user,
        name="remote_user_ignore",
    ),
    # Unignore (re-check) a remote user
    path(
        "<str:session_id>/remote-users/<str:user_name>/unignore/",
        remote_user_views.unignore_remote_user,
        name="remote_user_unignore",
    ),
    # Override session quorum mode (all | first_win)
    path(
        "<str:session_id>/remote-users/quorum/",
        remote_user_views.set_session_quorum_view,
        name="remote_user_quorum",
    ),
]

# Public join routes — included under /remote/ in config/urls.py
join_urlpatterns = [
    path("join/<str:token>/", remote_user_views.remote_user_join, name="remote_user_join"),
    path("join/<str:token>/online/", remote_user_views.remote_user_mark_online, name="remote_user_mark_online"),
    path("join/<str:token>/attachments/", remote_user_views.remote_user_upload_attachments, name="remote_user_upload_attachments"),
    path("join/<str:token>/attachments/<str:attachment_id>/delete/", remote_user_views.remote_user_delete_attachment, name="remote_user_delete_attachment"),
    path("join/<str:token>/respond/", remote_user_views.remote_user_respond, name="remote_user_respond"),
]
