"""
Langfuse tracing wiring via OpenTelemetry.

Env-gated: requires LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY. Optional
LANGFUSE_HOST (default https://cloud.langfuse.com).

When env vars are missing, init_tracing() logs `tracing.disabled` and returns
without configuring an exporter. Failures during init are logged via
`logger.exception` and never propagate.

Once initialized, the global OpenTelemetry TracerProvider is set; AutoGen 0.4+
SingleThreadedAgentRuntime picks up the tracer provider passed explicitly or
falls back to the global one used by manual spans.
"""

import base64
import logging
import os
from threading import Lock

logger = logging.getLogger(__name__)

_initialized = False
_lock = Lock()
_tracer_provider = None


def get_tracer_provider():
    """Return the configured TracerProvider, or None when tracing is disabled."""
    return _tracer_provider


def init_tracing() -> bool:
    """Initialize Langfuse OTLP exporter once per process. Returns True on success."""
    global _initialized, _tracer_provider

    with _lock:
        if _initialized:
            return _tracer_provider is not None

        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip().rstrip("/")

        if not public_key or not secret_key:
            logger.info("tracing.disabled", extra={"reason": "missing_langfuse_credentials"})
            _initialized = True
            return False

        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except Exception:
            logger.exception("tracing.import_failed")
            _initialized = True
            return False

        try:
            auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
            exporter = OTLPSpanExporter(
                endpoint=f"{host}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {auth}"},
            )
            resource = Resource.create({
                "service.name": os.getenv("OTEL_SERVICE_NAME", "product-discovery"),
            })
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            _tracer_provider = provider
            logger.info(
                "tracing.enabled",
                extra={"host": host, "service_name": resource.attributes.get("service.name")},
            )
        except Exception:
            logger.exception("tracing.setup_failed")
            _initialized = True
            return False

        _initialized = True
        return True
