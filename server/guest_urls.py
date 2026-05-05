"""Guest readonly watcher URL routes."""

from django.urls import path

from . import guest_views

# Host-facing routes — included under chat/sessions/ in server/urls.py
urlpatterns = [
    path(
        "<str:session_id>/guest/invite/",
        guest_views.generate_guest_invite_link,
        name="guest_invite",
    ),
    path(
        "<str:session_id>/guest/revoke/",
        guest_views.revoke_guest_invite_link,
        name="guest_revoke",
    ),
]

# Public guest join routes — included under /guest/ in config/urls.py
join_urlpatterns = [
    path("join/<str:token>/", guest_views.guest_join, name="guest_join"),
]
