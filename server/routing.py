"""WebSocket URL routing for server app."""

from django.urls import path

from server.consumers import (
    GuestChatConsumer,
    HostSessionConsumer,
    OAuthReadinessConsumer,
    RemoteChatConsumer,
    RemoteUserReadinessConsumer,
)

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
    # Host-facing realtime session feed (agent + remote user messages + quorum updates)
    path(
        "ws/session/<str:session_id>/",
        HostSessionConsumer.as_asgi(),
    ),
    # Remote-user-facing: receive live agent messages (public, token-gated)
    path(
        "ws/remote/chat/<str:token>/",
        RemoteChatConsumer.as_asgi(),
    ),
    # Guest-facing readonly chat feed (public, token-gated)
    path(
        "ws/guest/chat/<str:token>/",
        GuestChatConsumer.as_asgi(),
    ),
]
