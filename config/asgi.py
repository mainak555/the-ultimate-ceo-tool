"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os
import warnings

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

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

application = get_asgi_application()
