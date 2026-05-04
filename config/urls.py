from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import include, path
from django.views.generic.base import RedirectView

from server.remote_user_urls import join_urlpatterns as _remote_join_patterns

urlpatterns = [
    path(
        "favicon.ico",
        RedirectView.as_view(url=staticfiles_storage.url("assets/favicon.png"), permanent=True),
    ),
    path("", include("server.urls")),
    # Public remote-user join page (no APP_SECRET_KEY required)
    path("remote/", include((_remote_join_patterns, "remote_user"))),
]
