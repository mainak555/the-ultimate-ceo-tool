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
from contextlib import contextmanager
import json
import logging
import os
import re
from threading import Lock
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_initialized = False
_lock = Lock()
_tracer_provider = None
_event_bridge_installed = False


_MARKDOWN_PATTERN = re.compile(
    r"(^\s{0,3}#{1,6}\s+)|(^\s{0,3}[-*+]\s+)|(^\s{0,3}>\s+)|(```)|(`[^`]+`)|(\[[^\]]+\]\([^\)]+\))",
    re.MULTILINE,
)


def _infer_mime_type(value: Any) -> str:
    """Infer a payload MIME type for generic trace attributes."""
    if isinstance(value, (dict, list, tuple)):
        return "application/json"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "text/plain"
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list, tuple)):
                return "application/json"
        except Exception:
            pass
        if _MARKDOWN_PATTERN.search(text):
            return "text/markdown"
        return "text/plain"
    return "text/plain"


def _stringify_payload(value: Any) -> str:
    """Convert payload values to raw strings for span attributes."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _is_agent_llm_span(span: Any) -> bool:
    """Return True when a span belongs to Agent/LLM execution scope."""
    name = str(getattr(span, "name", "")).lower()
    attributes = getattr(span, "attributes", {}) or {}
    scope = getattr(span, "instrumentation_scope", None)
    scope_name = str(getattr(scope, "name", "")).lower() if scope is not None else ""

    # AutoGen-instrumented spans are always in scope for Langfuse.
    if scope_name.startswith("autogen"):
        return True

    # Manual allowlist for agent-owned LLM spans.
    if name == "agents.extraction.run":
        return True

    gen_ai_system = str(attributes.get("gen_ai.system", "")).lower()
    app_component = str(attributes.get("app.component", "")).lower()
    if name.startswith("agents.llm.") and gen_ai_system:
        return True
    if name.startswith("agents.") and app_component.startswith("agents.") and gen_ai_system:
        return True

    return False


class AgentLlmFilteringExporter:
    """Forward only Agent/LLM spans to the wrapped exporter."""

    def __init__(self, delegate: Any, span_export_result_success: Any):
        self._delegate = delegate
        self._success = span_export_result_success

    def export(self, spans: Any) -> Any:
        allowed = [span for span in spans if _is_agent_llm_span(span)]
        if not allowed:
            return self._success
        return self._delegate.export(tuple(allowed))

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        force_flush = getattr(self._delegate, "force_flush", None)
        if callable(force_flush):
            try:
                return bool(force_flush(timeout_millis=timeout_millis))
            except TypeError:
                return bool(force_flush())
        return True


class AutoGenEventSpanBridgeHandler(logging.Handler):
    """Convert AutoGen structured event logs into OTel spans.

    AutoGen emits rich LLM input/output payloads through `autogen_core.events`
    and `autogen_agentchat.events`. This handler bridges those events into trace
    spans so payload detail is visible in Langfuse without printing INFO payload
    dumps to console.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
        except Exception:
            return

        tracer = trace.get_tracer("autogen.events.bridge")
        raw_message = record.getMessage()

        event_data: dict[str, Any]
        try:
            parsed = json.loads(raw_message)
            event_data = parsed if isinstance(parsed, dict) else {"message": raw_message}
        except Exception:
            event_data = {"message": raw_message}

        event_type = str(event_data.get("type", "event")).lower()
        span_name = f"autogen.event.{event_type}"

        attrs: dict[str, Any] = {
            "gen_ai.system": "autogen",
            "autogen.event.type": event_data.get("type", "event"),
            "autogen.event.logger": record.name,
            "autogen.event.level": record.levelname,
            "langfuse.observation.metadata.autogen_event_raw": raw_message,
        }

        if "agent_id" in event_data and event_data.get("agent_id") is not None:
            attrs["autogen.agent.id"] = str(event_data["agent_id"])
        if "prompt_tokens" in event_data:
            attrs["gen_ai.usage.prompt_tokens"] = int(event_data["prompt_tokens"])
        if "completion_tokens" in event_data:
            attrs["gen_ai.usage.completion_tokens"] = int(event_data["completion_tokens"])

        messages = event_data.get("messages")
        if messages is not None:
            attrs["input.value"] = _stringify_payload(messages)
            attrs["input.mime_type"] = _infer_mime_type(messages)
        response = event_data.get("response")
        if response is not None:
            attrs["output.value"] = _stringify_payload(response)
            attrs["output.mime_type"] = _infer_mime_type(response)

        if "tool_name" in event_data:
            attrs["gen_ai.tool.name"] = str(event_data.get("tool_name"))
        if "arguments" in event_data:
            attrs["gen_ai.tool.arguments"] = _stringify_payload(event_data.get("arguments"))
        if "result" in event_data:
            attrs["gen_ai.tool.result"] = _stringify_payload(event_data.get("result"))

        with tracer.start_as_current_span(span_name) as span:
            for key, value in attrs.items():
                if value is None:
                    continue
                try:
                    span.set_attribute(key, value)
                except Exception:
                    # Best-effort only: drop non-serializable attributes.
                    continue

            if record.levelno >= logging.ERROR:
                span.set_status(Status(StatusCode.ERROR, raw_message[:300]))


def _install_autogen_event_bridge() -> None:
    """Attach a tracing bridge for AutoGen event payload logs."""
    global _event_bridge_installed
    if _event_bridge_installed:
        return

    bridge_handler = AutoGenEventSpanBridgeHandler()

    for logger_name in ("autogen_core.events", "autogen_agentchat.events"):
        event_logger = logging.getLogger(logger_name)

        # Keep event generation enabled for trace bridging.
        event_logger.setLevel(logging.INFO)
        event_logger.propagate = False

        # Keep console behavior ERROR-only while allowing INFO to bridge traces.
        for handler in event_logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.ERROR)

        event_logger.addHandler(bridge_handler)

    _event_bridge_installed = True


def get_tracer_provider():
    """Return the configured TracerProvider, or None when tracing is disabled."""
    return _tracer_provider


def is_tracing_enabled() -> bool:
    """Return True when an OTLP exporter has been configured successfully."""
    return _tracer_provider is not None


@contextmanager
def traced_block(span_name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Create a best-effort OpenTelemetry span for manual trace coverage.

    This helper never raises if OpenTelemetry is unavailable; callers can use it
    safely around critical paths like standalone extractor calls.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode
    except Exception:
        yield None
        return

    tracer = trace.get_tracer("product-discovery")
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is None:
                    continue
                span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


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
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
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
            filtered_exporter: Any = AgentLlmFilteringExporter(exporter, SpanExportResult.SUCCESS)
            resource = Resource.create({
                "service.name": os.getenv("OTEL_SERVICE_NAME", "product-discovery"),
            })
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(filtered_exporter))
            trace.set_tracer_provider(provider)
            _tracer_provider = provider
            _install_autogen_event_bridge()
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
