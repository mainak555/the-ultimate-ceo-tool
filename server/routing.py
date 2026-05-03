"""WebSocket URL routing for server app."""

from django.urls import path

from server.consumers import OAuthReadinessConsumer

websocket_urlpatterns = [
    path(
        "ws/mcp/oauth/<str:session_id>/",
        OAuthReadinessConsumer.as_asgi(),
    ),
]
