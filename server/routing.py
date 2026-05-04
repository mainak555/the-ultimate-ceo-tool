"""WebSocket URL routing for server app."""

from django.urls import path

from server.consumers import OAuthReadinessConsumer, RemoteChatConsumer, RemoteUserReadinessConsumer

websocket_urlpatterns = [
    path(
        "ws/mcp/oauth/<str:session_id>/",
        OAuthReadinessConsumer.as_asgi(),
    ),
    # Host-facing: watch remote users joining (requires ?skey=<APP_SECRET_KEY>)
    path(
        "ws/remote-users/<str:session_id>/",
        RemoteUserReadinessConsumer.as_asgi(),
    ),
    # Remote-user-facing: receive live agent messages (public, token-gated)
    path(
        "ws/remote/chat/<str:token>/",
        RemoteChatConsumer.as_asgi(),
    ),
]
