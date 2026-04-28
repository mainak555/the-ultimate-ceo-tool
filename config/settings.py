"""
Django settings for config project.

- Env vars read via os.getenv() (with defaults for local dev).
- For local dev, .env file is loaded automatically if present.
- Django SECRET_KEY is auto-generated each startup (no persistent sessions needed).
- No DATABASES — MongoDB via PyMongo only.
"""

import os
from pathlib import Path
from django.core.management.utils import get_random_secret_key

# ---------------------------------------------------------------------------
# Load .env file for local development (in production, env vars are injected)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
_env_path = BASE_DIR / ".env"
if _env_path.is_file():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# ---------------------------------------------------------------------------
# Core settings
# ---------------------------------------------------------------------------
SECRET_KEY = get_random_secret_key()

DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")

# Admin password for write access to configurations
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "")

# MongoDB connection
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_NAME = os.getenv("MONGODB_NAME", "product_discovery")

# Redis session coordination (active run lease/cancel signaling)
REDIS_URI = os.getenv("REDIS_URI", "redis://localhost:6379/0")
REDIS_NAMESPACE = os.getenv("REDIS_NAMESPACE", "product_discovery")
REDIS_SOCKET_TIMEOUT = float(os.getenv("REDIS_SOCKET_TIMEOUT", "2.0"))
REDIS_SOCKET_CONNECT_TIMEOUT = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "2.0"))
REDIS_RUN_LEASE_TTL_SECONDS = int(os.getenv("REDIS_RUN_LEASE_TTL_SECONDS", "300"))
REDIS_RUN_HEARTBEAT_SECONDS = int(os.getenv("REDIS_RUN_HEARTBEAT_SECONDS", "20"))
REDIS_CANCEL_SIGNAL_TTL_SECONDS = int(os.getenv("REDIS_CANCEL_SIGNAL_TTL_SECONDS", "120"))
# How long extracted attachment text is kept in Redis (seconds). Default 24 h.
# Raise this if sessions span multiple days; lower it to reduce Redis memory use.
REDIS_ATTACHMENT_TTL_SECONDS = int(os.getenv("REDIS_ATTACHMENT_TTL_SECONDS", "86400"))
# Maximum byte size of a serialized AutoGen agent state that can be persisted
# inside the chat_sessions MongoDB document (16 MB limit shared with discussions[]).
# Raise for long sessions with many attachments; lower to reduce document size.
MAX_AGENT_STATE_BYTES = int(os.getenv("MAX_AGENT_STATE_BYTES", "1000000"))  # default 1 MB

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    # Third-party
    "django_htmx",
    "compressor",
    # Project
    "server",
]

MIDDLEWARE = [
    "server.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database — not used (MongoDB via PyMongo in backend/db.py)
# ---------------------------------------------------------------------------
DATABASES = {}

# Session engine — use cookie-based (no DB needed)
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files & django-compressor
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "compressor.finders.CompressorFinder",
]

# WhiteNoise — serve static files in production
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# django-compressor + libsass
COMPRESS_PRECOMPILERS = (
    ("text/x-scss", "django_libsass.SassCompiler"),
)
COMPRESS_ENABLED = True
COMPRESS_OFFLINE = not DEBUG

# ---------------------------------------------------------------------------
# Logging — JSON-structured stderr; request-id propagation; per-package levels
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "server.logging_utils.RequestIdFilter",
        },
        "trace_context": {
            "()": "server.logging_utils.TraceContextFilter",
        },
        "event_only": {
            "()": "server.logging_utils.EventOnlyConsoleFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "server.logging_utils.JsonFormatter",
            "format": "%(timestamp)s %(level)s %(logger)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id", "trace_context", "event_only"],
        },
    },
    "loggers": {
        "server": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "agents": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        # NOTE: `autogen_core.events` and `autogen_agentchat.events` are
        # intentionally NOT listed here. `core.tracing._install_autogen_event_bridge`
        # owns those loggers — it strips the shared `console` handler and
        # attaches the span-bridge handler instead. INFO payload events flow
        # to spans (with redaction + truncation), never to console.
        "autogen_core": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "autogen_agentchat": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "pymongo": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "urllib3": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "httpx": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}
