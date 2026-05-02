from django.urls import re_path

from .consumers import LeaderReadinessConsumer, RemoteUserConsumer

websocket_urlpatterns = [
    re_path(r"^ws/chat/(?P<session_id>[^/]+)/leader/$", LeaderReadinessConsumer.as_asgi()),
    re_path(r"^ws/chat/(?P<session_id>[^/]+)/remote-user/(?P<token>[^/]+)/$", RemoteUserConsumer.as_asgi()),
]
