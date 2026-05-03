"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os
import warnings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from server.routing import websocket_urlpatterns

# Static file responses can use sync streaming iterators; under ASGI Django
# emits a warning when adapting them. Suppress this known noisy warning.
warnings.filterwarnings(
	"ignore",
	message=(
		"StreamingHttpResponse must consume synchronous iterators in order "
		"to serve them asynchronously.*"
	),
	category=Warning,
)

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
