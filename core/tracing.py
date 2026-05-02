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
- ``OTEL_INSTRUMENT_MONGO`` — pymongo auto-instrumentation (one span per
  Mongo command — high cardinality). Default ``off``; set to ``1``/``true``
  to enable when diagnosing query/connection issues.
- ``OTEL_INSTRUMENT_AGENTS`` — AutoGen event-log → span bridge (LLM calls,
  prompts, tool invocations). Default ``on``; set to ``0``/``false`` to
  silence the LLM/agent layer.
- ``OTEL_INSTRUMENT_WEBSOCKET`` — manual websocket spans (``ws.*`` in
    Channels consumers). Default ``off``; set to ``1``/``true`` to enable
    websocket trace noise when debugging realtime flows.

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
from datetime import datetime, timezone
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

    def _json_default(obj: Any) -> str:
        if isinstance(obj, datetime):
            if obj.tzinfo is None:
                obj = obj.replace(tzinfo=timezone.utc)
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    try:
        return json.dumps(value, ensure_ascii=False, default=_json_default)
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
    """Convert AutoGen structured event logs into OTel spans.

    Accepts DEBUG-level records so that ``ToolCallRequestEvent`` and
    ``ToolCallExecutionEvent`` — which AutoGen emits at DEBUG — are captured
    alongside the INFO-level ``LLMCallEvent`` records from the model client.
    """

    def __init__(self) -> None:
        # Must be DEBUG: autogen_agentchat.events logs tool events at DEBUG.
        super().__init__(level=logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
        except Exception:
            return

        tracer = trace.get_tracer("autogen.events.bridge")

        # autogen_agentchat.events records carry Pydantic BaseAgentEvent
        # instances as record.msg; their __str__ is a Pydantic repr, NOT JSON.
        # autogen_core.events records (LLMCallEvent etc.) define __str__ to
        # return json.dumps(...) — use getMessage() for those.
        event_data: dict[str, Any]
        original_msg = record.msg
        if hasattr(original_msg, "model_dump"):
            try:
                event_data = original_msg.model_dump()
            except Exception:
                event_data = {"message": str(original_msg)}
        else:
            raw_message = record.getMessage()
            try:
                parsed = json.loads(raw_message)
                event_data = parsed if isinstance(parsed, dict) else {"message": raw_message}
            except Exception:
                event_data = {"message": raw_message}

        # Canonical raw representation for the span metadata attribute.
        try:
            raw_message = json.dumps(event_data)
        except Exception:
            raw_message = str(event_data)

        event_type_raw = str(event_data.get("type", "event"))
        event_type = event_type_raw.lower()

        # Base attributes shared by every span this event produces.
        # `source` names the emitting agent on agentchat events;
        # `agent_id` is the legacy field used by autogen_core events.
        base_attrs: dict[str, Any] = {
            "gen_ai.system": "autogen",
            "autogen.event.type": event_type_raw,
            "autogen.event.logger": record.name,
            "autogen.event.level": record.levelname,
            "langfuse.observation.metadata.autogen_event_raw": raw_message,
        }
        source = event_data.get("source") or event_data.get("agent_id")
        if source is not None:
            base_attrs["autogen.agent.id"] = str(source)
        if "prompt_tokens" in event_data:
            base_attrs["gen_ai.usage.prompt_tokens"] = int(event_data["prompt_tokens"])
        if "completion_tokens" in event_data:
            base_attrs["gen_ai.usage.completion_tokens"] = int(event_data["completion_tokens"])

        def _apply_base(span: Any) -> None:
            for k, v in base_attrs.items():
                if v is None:
                    continue
                try:
                    span.set_attribute(k, v)
                except Exception:
                    continue

        # ------------------------------------------------------------------
        # ToolCallRequestEvent — LLM requested one or more tool calls.
        # AutoGen shape (ToolCallRequestEvent.content: List[FunctionCall]):
        #   {"type": "ToolCallRequestEvent", "source": "<agent>",
        #    "content": [{"id": "call_xxx", "name": "<tool>", "arguments": "..."}]}
        # One span per item so each tool call is independently visible.
        # Logged at DEBUG by autogen_agentchat — bridge is set to DEBUG.
        # ------------------------------------------------------------------
        if event_type == "toolcallrequestevent":
            content = event_data.get("content") or [{}]
            for item in content:
                tool_name = str(item.get("name") or "unknown_tool")
                call_id = str(item.get("id") or "")
                arguments = item.get("arguments")
                with tracer.start_as_current_span(f"mcp.tool.request {tool_name}") as span:
                    _apply_base(span)
                    try:
                        span.set_attribute("gen_ai.tool.name", tool_name)
                        if call_id:
                            span.set_attribute("gen_ai.tool.call.id", call_id)
                    except Exception:
                        pass
                    if arguments is not None:
                        # input.value surfaces in Langfuse "Input" panel
                        set_payload_attribute(span, "input.value", arguments)
                        set_payload_attribute(span, "gen_ai.tool.arguments", arguments)
                    if record.levelno >= logging.ERROR:
                        span.set_status(Status(StatusCode.ERROR, raw_message[:300]))
            return

        # ------------------------------------------------------------------
        # ToolCallExecutionEvent — tool returned a response to the agent.
        # AutoGen shape (ToolCallExecutionEvent.content: List[FunctionExecutionResult]):
        #   {"type": "ToolCallExecutionEvent", "source": "<agent>",
        #    "content": [{"call_id": "call_xxx", "name": "<tool>",
        #                 "content": "<result>", "is_error": false}]}
        # Logged at DEBUG by autogen_agentchat — bridge is set to DEBUG.
        # is_error=True marks the span ERROR so failures surface immediately.
        # ------------------------------------------------------------------
        if event_type == "toolcallexecutionevent":
            content = event_data.get("content") or [{}]
            for item in content:
                tool_name = str(item.get("name") or "unknown_tool")
                call_id = str(item.get("call_id") or "")
                result = item.get("content")
                is_error = bool(item.get("is_error", False))
                with tracer.start_as_current_span(f"mcp.tool.result {tool_name}") as span:
                    _apply_base(span)
                    try:
                        span.set_attribute("gen_ai.tool.name", tool_name)
                        if call_id:
                            span.set_attribute("gen_ai.tool.call.id", call_id)
                        span.set_attribute("gen_ai.tool.is_error", is_error)
                    except Exception:
                        pass
                    if result is not None:
                        # output.value surfaces in Langfuse "Output" panel
                        set_payload_attribute(span, "output.value", result)
                        set_payload_attribute(span, "gen_ai.tool.result", result)
                    if is_error:
                        span.set_status(
                            Status(StatusCode.ERROR, str(result)[:300] if result else "tool_error")
                        )
                    elif record.levelno >= logging.ERROR:
                        span.set_status(Status(StatusCode.ERROR, raw_message[:300]))
            return

        # ------------------------------------------------------------------
        # Generic events (LLMCallEvent, ThoughtEvent, streaming chunks, etc.)
        # ------------------------------------------------------------------
        with tracer.start_as_current_span(f"autogen.event.{event_type}") as span:
            _apply_base(span)

            messages = event_data.get("messages")
            if messages is not None:
                set_payload_attribute(span, "input.value", messages)
            response = event_data.get("response")
            if response is not None:
                set_payload_attribute(span, "output.value", response)

            # Fallback: top-level tool fields emitted by non-standard events.
            if "tool_name" in event_data:
                try:
                    span.set_attribute("gen_ai.tool.name", str(event_data["tool_name"]))
                except Exception:
                    pass
            if "arguments" in event_data:
                set_payload_attribute(span, "gen_ai.tool.arguments", event_data["arguments"])
            if "result" in event_data:
                set_payload_attribute(span, "gen_ai.tool.result", event_data["result"])

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
        # Must be DEBUG: autogen_agentchat.events logs ToolCallRequestEvent
        # and ToolCallExecutionEvent at DEBUG level. Lowering to DEBUG here
        # does not affect other namespaces because propagate=False below
        # prevents these records from reaching the root logger / console.
        # autogen_core.events records (LLMCallEvent) are INFO — unaffected.
        event_logger.setLevel(logging.DEBUG)
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


def start_root_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> tuple[Any, str | None]:
    """Start a new OTel root span (no parent) and return ``(span, traceparent)``.

    The span is created with an empty context — intentionally severing the
    active Django request span as parent — so every call produces a fresh
    ``trace_id``. This is the correct model for agent session runs: each
    ``/run/`` round is its own trace, independently queryable in Langfuse.

    The returned ``traceparent`` is the W3C header string
    (``00-<trace_id>-<span_id>-01``) extracted from the span's context via
    ``propagate.inject``. It can be stored in Redis (see
    ``agents/session_coordination.py``) and reattached inside the SSE
    ``event_stream()`` generator after the Django middleware ``finally`` block
    clears the request span context.

    The caller is responsible for calling ``span.end()`` after the run
    completes. ``@traced_function`` spans created while the span is attached
    (via ``otel_context.attach(trace.set_span_in_context(span))``) will
    automatically nest as children.

    Returns ``(None, None)`` when OTel is unavailable or tracing is disabled
    (no ``LANGFUSE_*`` / OTLP exporter configured).
    """
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.context import Context
    except Exception:
        return None, None

    tracer = trace.get_tracer("product-discovery")
    # Context() is an empty immutable context — no active span — so the new
    # span becomes a root with a fresh trace_id (no Django request parent).
    span = tracer.start_span(name, context=Context())

    if not span.is_recording():
        # TracerProvider not configured (tracing disabled).
        try:
            span.end()
        except Exception:  # noqa: BLE001
            pass
        return None, None

    if attributes:
        for k, v in (attributes or {}).items():
            try:
                span.set_attribute(k, v)
            except Exception:  # noqa: BLE001
                pass

    # Extract W3C traceparent from the span so it can be stored externally.
    carrier: dict[str, str] = {}
    try:
        propagate.inject(carrier, context=trace.set_span_in_context(span))
    except Exception:  # noqa: BLE001
        pass

    return span, carrier.get("traceparent") or None


def context_from_traceparent(traceparent: str) -> Any | None:
    """Reconstruct an OTel context from a W3C ``traceparent`` string.

    Returns a context containing the remote span described by ``traceparent``.
    Attaching this context and then making ``span`` current via
    ``trace.set_span_in_context`` restores the full parent chain so child
    spans nest correctly in the OTLP backend.

    Returns ``None`` when ``traceparent`` is empty/malformed or OTel is
    unavailable.
    """
    if not traceparent:
        return None
    try:
        from opentelemetry import propagate
        return propagate.extract({"traceparent": traceparent})
    except Exception:  # noqa: BLE001
        return None


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
    explicitly via ``OTEL_INSTRUMENT_MONGO=1`` when diagnosing query or
    connection issues.
    """
    return _flag_enabled("OTEL_INSTRUMENT_MONGO", default=False)


def _agents_tracing_enabled() -> bool:
    """AutoGen event-log -> span bridge (default on).

    Disable with ``OTEL_INSTRUMENT_AGENTS=0`` to silence LLM call,
    prompt, and tool spans without touching the rest of the trace.
    """
    return _flag_enabled("OTEL_INSTRUMENT_AGENTS", default=True)


def websocket_tracing_enabled() -> bool:
    """Manual websocket spans in Channels consumers (default off)."""
    return _flag_enabled("OTEL_INSTRUMENT_WEBSOCKET", default=False)


def _wire_auto_instrumentation() -> None:
    """Enable OpenTelemetry auto-instrumentation per category toggles.

    - HTTP    (Django + requests): ``OTEL_INSTRUMENT_HTTP``    (default on)
    - PYMONGO (pymongo):           ``OTEL_INSTRUMENT_MONGO`` (default off)
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
                    "instrument_websocket": websocket_tracing_enabled(),
                },
            )
        except Exception:
            logger.exception("tracing.setup_failed")
            _initialized = True
            return False

        _initialized = True
        return True
