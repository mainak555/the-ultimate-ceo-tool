"""OpenTelemetry tracing for the product-discovery app.

OpenTelemetry is the project-wide observability standard. The OTLP exporter
is pluggable: Langfuse is the currently-wired backend, but swapping to a
generic OTLP collector (Tempo / Jaeger / Honeycomb / etc.) is mechanical —
replace ``_build_langfuse_exporter()`` with a sibling helper that reads
``OTEL_EXPORTER_OTLP_ENDPOINT`` + ``OTEL_EXPORTER_OTLP_HEADERS`` and return
that from ``_build_exporter()``. No call sites change.

What this module owns
---------------------
- ``init_tracing()`` — one-shot wiring (TracerProvider, exporter, auto-instr,
  AutoGen event bridge). Called once from ``server/apps.py`` ``ServerConfig.ready``.
- ``traced_block(name, attributes)`` — context manager for manual spans.
- ``traced_function(name, attributes)`` — decorator wrapper around
  ``traced_block`` for service-layer functions.
- ``redact_payload(value)`` — deep-walks dict/list/str and masks any field
  matching the secret-key regex. Used before setting ``input.value`` /
  ``output.value`` on spans.
- ``truncate_for_span(text)`` — caps payloads at ``OTEL_MAX_PAYLOAD_BYTES``
  bytes (default 32 KB). Returns ``(text, truncated, original_bytes)``.

Env vars
--------
- ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` — required to enable export.
  Missing → ``tracing.disabled`` info log, no exporter, no errors.
- ``LANGFUSE_HOST`` — default ``https://cloud.langfuse.com``.
- ``OTEL_SERVICE_NAME`` — default ``product-discovery``.
- ``OTEL_MAX_PAYLOAD_BYTES`` — payload truncation cap (default 32768).
- ``OTEL_CONSOLE_EXPORTER`` — span console output mode:
    * ``off`` (default): no spans on console; OTLP backend still receives all spans.
    * ``error``: print only ERROR-status spans (full attributes + recorded
      exception + stacktrace) to stderr. Useful in production for
      diagnosing failures without log scraping.
    * ``all`` / ``true`` / ``1``: print every finished span to stderr (very
      noisy — dev-only). Implicitly enabled when ``LOG_LEVEL=DEBUG``.
- ``OTEL_INSTRUMENT_HTTP`` — Django + outbound ``requests`` auto-instrumentation
  (app & API spans). Default ``on``; set to ``0``/``false``/``off`` to disable.
- ``OTEL_INSTRUMENT_PYMONGO`` — pymongo auto-instrumentation (one span per
  Mongo command — high cardinality). Default ``off``; set to ``1``/``true``
  to enable when diagnosing query/connection issues.
- ``OTEL_INSTRUMENT_AGENTS`` — AutoGen event-log → span bridge (LLM calls,
  prompts, tool invocations). Default ``on``; set to ``0``/``false`` to
  silence the LLM/agent layer.

Boundaries
----------
Logs always go to console (stderr / JSON formatter). Tracing is strictly
opt-in: when ``init_tracing`` is disabled, every helper still works as a
no-op so callers never need to feature-flag their span code.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import os
import re
from contextlib import contextmanager
from threading import Lock
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_initialized = False
_lock = Lock()
_tracer_provider: Any = None
_event_bridge_installed = False


# ---------------------------------------------------------------------------
# Payload helpers (MIME inference, redaction, truncation)
# ---------------------------------------------------------------------------
_MARKDOWN_PATTERN = re.compile(
    r"(^\s{0,3}#{1,6}\s+)|(^\s{0,3}[-*+]\s+)|(^\s{0,3}>\s+)|(```)|(`[^`]+`)|(\[[^\]]+\]\([^\)]+\))",
    re.MULTILINE,
)

_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|authorization|x[-_]app[-_]secret[-_]key)"
)

_DEFAULT_MAX_PAYLOAD_BYTES = 32 * 1024


def _infer_mime_type(value: Any) -> str:
    """Infer a payload MIME type for span attributes."""
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


def redact_payload(value: Any) -> Any:
    """Deep-redact any value before serializing it onto a span attribute.

    Walks dicts/lists and masks values whose key matches the secret-key
    pattern. Strings are returned unchanged (URL-level secret stripping for
    Trello query params lives in the client module's ``_redact_url`` helper).
    """
    if isinstance(value, dict):
        return {
            k: ("***" if (isinstance(k, str) and _SECRET_KEY_RE.search(k)) else redact_payload(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(v) for v in value)
    return value


def _max_payload_bytes() -> int:
    raw = os.getenv("OTEL_MAX_PAYLOAD_BYTES", "").strip()
    if not raw:
        return _DEFAULT_MAX_PAYLOAD_BYTES
    try:
        n = int(raw)
        return n if n > 0 else _DEFAULT_MAX_PAYLOAD_BYTES
    except ValueError:
        return _DEFAULT_MAX_PAYLOAD_BYTES


def truncate_for_span(text: str) -> tuple[str, bool, int]:
    """Cap a payload string at ``OTEL_MAX_PAYLOAD_BYTES`` (default 32 KB).

    Returns ``(maybe_truncated_text, was_truncated, original_byte_length)``.
    Callers that truncate should also set ``span.body.truncated=true`` and
    ``span.body.original_bytes=<n>`` attributes on the span.
    """
    if text is None:
        return "", False, 0
    if not isinstance(text, str):
        text = _stringify_payload(text)
    encoded = text.encode("utf-8", errors="replace")
    cap = _max_payload_bytes()
    if len(encoded) <= cap:
        return text, False, len(encoded)
    truncated = encoded[:cap].decode("utf-8", errors="ignore")
    return truncated, True, len(encoded)


def set_payload_attribute(span: Any, key: str, value: Any) -> None:
    """Set a redacted, truncated payload attribute on a span.

    ``key`` is typically ``"input.value"`` or ``"output.value"``. The matching
    ``<key prefix>.mime_type`` attribute is set automatically. When the value
    is truncated, ``span.body.truncated`` and ``span.body.original_bytes``
    are also set.
    """
    if span is None or value is None:
        return
    redacted = redact_payload(value)
    text = _stringify_payload(redacted)
    truncated_text, was_truncated, original_bytes = truncate_for_span(text)
    try:
        span.set_attribute(key, truncated_text)
        prefix = key.rsplit(".", 1)[0]
        span.set_attribute(f"{prefix}.mime_type", _infer_mime_type(redacted))
        if was_truncated:
            span.set_attribute("span.body.truncated", True)
            span.set_attribute("span.body.original_bytes", original_bytes)
    except Exception:
        # Best-effort: never let span instrumentation crash a real call path.
        pass


# ---------------------------------------------------------------------------
# AutoGen event-log → span bridge (kept from previous implementation)
# ---------------------------------------------------------------------------
class AutoGenEventSpanBridgeHandler(logging.Handler):
    """Convert AutoGen structured event logs into OTel spans."""

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

        with tracer.start_as_current_span(span_name) as span:
            for key, value in attrs.items():
                if value is None:
                    continue
                try:
                    span.set_attribute(key, value)
                except Exception:
                    continue

            messages = event_data.get("messages")
            if messages is not None:
                set_payload_attribute(span, "input.value", messages)
            response = event_data.get("response")
            if response is not None:
                set_payload_attribute(span, "output.value", response)

            if "tool_name" in event_data:
                try:
                    span.set_attribute("gen_ai.tool.name", str(event_data.get("tool_name")))
                except Exception:
                    pass
            if "arguments" in event_data:
                set_payload_attribute(span, "gen_ai.tool.arguments", event_data.get("arguments"))
            if "result" in event_data:
                set_payload_attribute(span, "gen_ai.tool.result", event_data.get("result"))

            if record.levelno >= logging.ERROR:
                span.set_status(Status(StatusCode.ERROR, raw_message[:300]))


def _install_autogen_event_bridge() -> None:
    """Attach the tracing bridge for AutoGen event payload logs."""
    global _event_bridge_installed
    if _event_bridge_installed:
        return

    bridge_handler = AutoGenEventSpanBridgeHandler()

    for logger_name in ("autogen_core.events", "autogen_agentchat.events"):
        event_logger = logging.getLogger(logger_name)
        # We need INFO records to reach the bridge handler (which converts
        # them to spans) but we DO NOT want them on console. Logger-level
        # filtering happens before per-handler filtering, so the logger must
        # be at INFO. To keep console quiet we drop any pre-attached stream
        # handlers (config wires the shared console handler here for ERROR
        # propagation; the bridge replaces that — ERROR records are still
        # surfaced because the bridge sets span status to ERROR and Django's
        # error middleware logs at the request layer).
        event_logger.setLevel(logging.INFO)
        event_logger.propagate = False
        # Strip any non-bridge handlers (notably the shared console handler
        # added by Django LOGGING) so AutoGen INFO payload events do not
        # pollute stderr. Mutating handler.setLevel here would change the
        # SHARED console handler used by every other namespace.
        event_logger.handlers = [
            h for h in event_logger.handlers if isinstance(h, AutoGenEventSpanBridgeHandler)
        ]
        event_logger.addHandler(bridge_handler)

    _event_bridge_installed = True


# ---------------------------------------------------------------------------
# Public span helpers
# ---------------------------------------------------------------------------
def get_tracer_provider() -> Any:
    """Return the configured TracerProvider, or None when tracing is disabled."""
    return _tracer_provider


def is_tracing_enabled() -> bool:
    """Return True when an OTLP exporter has been configured successfully."""
    return _tracer_provider is not None


@contextmanager
def traced_block(span_name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Open an OpenTelemetry span around a block of code.

    Safe no-op when OpenTelemetry isn't importable. Sets ERROR status and
    records the exception when the wrapped block raises.
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
                try:
                    span.set_attribute(key, value)
                except Exception:
                    continue
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def traced_function(
    span_name: str,
    attributes: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator wrapping a function in ``traced_block``.

    Usage:

        @traced_function("service.project.create")
        def create_project(...): ...
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with traced_block(span_name, attributes):
                return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Exporter wiring (Langfuse currently; pluggable by replacing _build_exporter)
# ---------------------------------------------------------------------------
def _build_langfuse_exporter() -> Any | None:
    """Return a Langfuse-backed OTLPSpanExporter, or None when env is missing."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip().rstrip("/")

    if not public_key or not secret_key:
        logger.info("tracing.disabled", extra={"reason": "missing_langfuse_credentials"})
        return None

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except Exception:
        logger.exception("tracing.import_failed", extra={"package": "otlp_http"})
        return None

    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return OTLPSpanExporter(
        endpoint=f"{host}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}"},
    )


def _build_exporter() -> Any | None:
    """Single entry point for the active OTLP exporter.

    Swap this implementation (or add branches that read
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` + ``OTEL_EXPORTER_OTLP_HEADERS``) to
    target a different OTLP backend without touching call sites.
    """
    return _build_langfuse_exporter()


def _resolve_console_span_mode() -> str:
    """Resolve the requested console span exporter mode.

    Returns one of ``"off"``, ``"error"``, or ``"all"``. ``LOG_LEVEL=DEBUG``
    implicitly upgrades the default to ``"all"`` so deep-dive sessions get
    every span on console without flipping a second flag.
    """
    raw = os.getenv("OTEL_CONSOLE_EXPORTER", "").strip().lower()
    if raw in ("1", "true", "yes", "all"):
        return "all"
    if raw in ("error", "errors", "err"):
        return "error"
    if raw in ("0", "false", "no", "off"):
        return "off"
    if os.getenv("LOG_LEVEL", "INFO").strip().upper() == "DEBUG":
        return "all"
    return "off"


def _build_console_span_processor() -> Any | None:
    """Return a SimpleSpanProcessor that prints spans to stderr, or None.

    Mode is resolved from ``OTEL_CONSOLE_EXPORTER`` / ``LOG_LEVEL``. The
    ``error`` mode wraps :class:`ConsoleSpanExporter` so only spans with
    ``StatusCode.ERROR`` are printed — recorded exceptions and the full
    span attribute set come along for free, giving an at-a-glance call
    stack on failure.
    """
    mode = _resolve_console_span_mode()
    if mode == "off":
        return None

    try:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
        from opentelemetry.trace import StatusCode
    except Exception:
        logger.exception("tracing.import_failed", extra={"package": "sdk_console_exporter"})
        return None

    if mode == "all":
        exporter: Any = ConsoleSpanExporter()
    else:
        class _ErrorOnlyConsoleSpanExporter(ConsoleSpanExporter):
            """Print only ERROR-status spans to stderr."""

            def export(self, spans):  # type: ignore[override]
                errs = [s for s in spans if s.status.status_code == StatusCode.ERROR]
                if not errs:
                    # Mirror parent's SUCCESS sentinel without importing it.
                    from opentelemetry.sdk.trace.export import SpanExportResult
                    return SpanExportResult.SUCCESS
                return super().export(errs)

        exporter = _ErrorOnlyConsoleSpanExporter()

    return SimpleSpanProcessor(exporter)


def _flag_enabled(env_var: str, *, default: bool) -> bool:
    """Parse a yes/no env var with the given default.

    Truthy: ``1``, ``true``, ``yes``, ``on``.
    Falsy:  ``0``, ``false``, ``no``, ``off``.
    Empty / missing / unrecognized -> ``default``.
    """
    raw = os.getenv(env_var, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _http_tracing_enabled() -> bool:
    """Django + outbound requests auto-instrumentation (default on)."""
    return _flag_enabled("OTEL_INSTRUMENT_HTTP", default=True)


def _pymongo_tracing_enabled() -> bool:
    """Pymongo auto-instrumentation (default off).

    Mongo spans are emitted per command and inflate trace volume. Enable
    explicitly via ``OTEL_INSTRUMENT_PYMONGO=1`` when diagnosing query or
    connection issues.
    """
    return _flag_enabled("OTEL_INSTRUMENT_PYMONGO", default=False)


def _agents_tracing_enabled() -> bool:
    """AutoGen event-log -> span bridge (default on).

    Disable with ``OTEL_INSTRUMENT_AGENTS=0`` to silence LLM call,
    prompt, and tool spans without touching the rest of the trace.
    """
    return _flag_enabled("OTEL_INSTRUMENT_AGENTS", default=True)


def _wire_auto_instrumentation() -> None:
    """Enable OpenTelemetry auto-instrumentation per category toggles.

    - HTTP    (Django + requests): ``OTEL_INSTRUMENT_HTTP``    (default on)
    - PYMONGO (pymongo):           ``OTEL_INSTRUMENT_PYMONGO`` (default off)
    """
    targets: list[tuple[str, str, str]] = []
    if _http_tracing_enabled():
        targets.extend([
            ("opentelemetry.instrumentation.django", "DjangoInstrumentor", "django"),
            ("opentelemetry.instrumentation.requests", "RequestsInstrumentor", "requests"),
        ])
    if _pymongo_tracing_enabled():
        targets.append(
            ("opentelemetry.instrumentation.pymongo", "PymongoInstrumentor", "pymongo")
        )

    for module_path, class_name, package in targets:
        try:
            module = __import__(module_path, fromlist=[class_name])
            getattr(module, class_name)().instrument()
        except Exception:
            logger.exception("tracing.instrument_failed", extra={"package": package})


def init_tracing() -> bool:
    """Initialize the OTLP exporter once per process. Returns True on success."""
    global _initialized, _tracer_provider

    with _lock:
        if _initialized:
            return _tracer_provider is not None

        exporter = _build_exporter()
        if exporter is None:
            _initialized = True
            return False

        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except Exception:
            logger.exception("tracing.import_failed", extra={"package": "sdk"})
            _initialized = True
            return False

        try:
            resource = Resource.create({
                "service.name": os.getenv("OTEL_SERVICE_NAME", "product-discovery"),
            })
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(exporter))

            console_processor = _build_console_span_processor()
            if console_processor is not None:
                provider.add_span_processor(console_processor)

            trace.set_tracer_provider(provider)
            _tracer_provider = provider

            _wire_auto_instrumentation()
            if _agents_tracing_enabled():
                _install_autogen_event_bridge()

            logger.info(
                "tracing.enabled",
                extra={
                    "service_name": resource.attributes.get("service.name"),
                    "max_payload_bytes": _max_payload_bytes(),
                    "console_span_mode": _resolve_console_span_mode(),
                    "instrument_http": _http_tracing_enabled(),
                    "instrument_pymongo": _pymongo_tracing_enabled(),
                    "instrument_agents": _agents_tracing_enabled(),
                },
            )
        except Exception:
            logger.exception("tracing.setup_failed")
            _initialized = True
            return False

        _initialized = True
        return True
