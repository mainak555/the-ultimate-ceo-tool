---
name: observability-logging
description: Use when adding HTTP clients, MongoDB access, service-layer code, AutoGen runtime code, model client wrappers, or any module that performs I/O. Enforces structured JSON logging via module loggers, request-ID propagation, secret redaction, OpenTelemetry tracing for HTTP/services/views/agents/Mongo, and the pluggable OTLP exporter pattern (Langfuse currently).
---

# Skill: Observability & Logging

## Purpose
Single source of truth for application logging and OpenTelemetry tracing.
Every new HTTP client, service module, MongoDB call, view, and agent runtime
entry point must conform to the rules below before merge.

OpenTelemetry is the project-wide observability standard. Logs and traces
serve different purposes:
- **Logs** (stderr JSON, `LOG_LEVEL` env): high-signal lifecycle events,
  function-entry markers, and errors. Optimized for human reading.
- **Traces** (OTLP exporter, currently Langfuse): full request/response
  payloads (redacted, truncated), call graph, timing. Optimized for tool
  consumption.

## Mandatory Logger Rules
1. Every Python module that performs I/O declares `logger = logging.getLogger(__name__)` at module top. No custom logger names. No `print()` for diagnostics.
2. Logging configuration lives only in `config/settings.py` (`LOGGING` dict). One stderr `StreamHandler` + JSON formatter (`python-json-logger`). Root level driven by `LOG_LEVEL` env (default `INFO`).
3. The `console` handler installs `EventOnlyConsoleFilter` from `server/logging_utils.py`, which drops INFO records whose message ends in `.api.call`. Per-call HTTP detail belongs on spans, not console.
4. AutoGen event payload loggers (`autogen_core.events`, `autogen_agentchat.events`) are owned by `_install_autogen_event_bridge()` in `core/tracing.py`, NOT by `config/settings.py` LOGGING. The bridge strips the shared `console` handler from those loggers and attaches `AutoGenEventSpanBridgeHandler` instead, so payload events flow to spans (with redaction + truncation) and never reach console. Both loggers are set to **DEBUG** so that `ToolCallRequestEvent` and `ToolCallExecutionEvent` (emitted at `logging.DEBUG` by `autogen_agentchat`) reach the bridge; `propagate=False` prevents those DEBUG records from flooding the root logger. `autogen_core.events` records (`LLMCallEvent`) are INFO — unaffected by the level change. Do not re-add `autogen_*.events` entries to the LOGGING dict.
5. Event names use dotted snake_case scoped by layer:
   - Service layer: `mongo.connect`, `mongo.connect_failed`, `project.created`, `chat.session.started`.
   - HTTP clients: `trello.api.error`, `jira.api.error` (success no longer logged — see rule 3).
   - Agent runtime: `agents.model_client.created`, `agents.team.built`, `agents.team.cancelled`, `agents.extraction.completed`, `agents.extraction.parse_failed`.
   - Tracing: `tracing.enabled`, `tracing.disabled`, `tracing.instrument_failed`, `tracing.setup_failed`.
6. Use `logger.info` for successful lifecycle events with structured context (`extra={...}`). Use `logger.warning` immediately before raising a `ValueError` for an expected business-rule failure. Use `logger.exception` for unexpected exceptions.
7. Never log full request/response payloads to console. Log identifiers, counts, status codes, and `elapsed_ms`. Body snippets allowed only on ERROR paths (≤ 500 chars, sanitized). Full bodies belong on spans.

## Mandatory Redaction Rules
1. Strip Trello `key=` and `token=` query parameters from any URL before logging — use `_redact_url()` in `server/trello_client.py`.
2. Never log the `Authorization` header, Basic-auth strings, API keys, OAuth tokens, passwords, or `X-App-Secret-Key`.
3. Span payload attributes must be set via `set_payload_attribute()` from `core/tracing.py`, which calls `redact_payload()` (masks any field whose key matches `(?i)api_key|secret|password|token|authorization|x-app-secret-key`) before serialization.
4. Validate diffs with `grep -nE "API_KEY|SECRET|password|Authorization|Bearer" <changed files>` and ensure no matches sit inside log strings.

## Service Layer Events Catalog
| Module | Required Events |
|--------|------------------|
| `server/db.py` | `mongo.connect` (INFO, first connect), `mongo.connect_failed` (EXCEPTION). Per-op spans come from `PymongoInstrumentor` auto-instrumentation. |
| `server/services.py` | `project.created`/`project.updated`/`project.deleted` (INFO with `project_id`), `chat.session.started`/`chat.session.ended` (INFO with `session_id`), validation `WARNING` before raising `ValueError`. Public mutation entry points wrapped with `@traced_function("service.<area>.<op>")`. |
| `server/trello_service.py`, `server/jira_service.py`, `server/jira_*_service.py` | Public extract/push/verify/metadata-fetch entry points wrapped with `@traced_function`. Credential resolution failures as `WARNING`; currently-swallowed `ValueError` fallbacks must call `logger.warning("...fallback", exc_info=True)` instead of silently passing. |

## HTTP Client Pattern (Trello / Jira / Future Providers)
The `requests` library is auto-instrumented; every outbound call already gets
a span. Client modules MUST use the shared helper in `core/http_tracing.py`
instead of implementing provider-local span helpers.

1. Use `instrument_http_response(resp, provider=..., action=...)` for every
   outbound response path (success and error).
2. Name response-handler helpers meaningfully (for example
   `_handle_api_response`) instead of generic names like `_check`.
3. For non-2xx responses, pass parsed details into the same helper:
   - `detail="..."`
   - optional `error_messages=[...]`
   - optional `field_errors={...}`
4. For fallback branches that intentionally continue (no raised exception),
   still call `instrument_http_response(..., detail=...)` so spans show full
   error body/details instead of metadata-only failures.
5. `OTEL_HTTP_LOG_BODY` controls whether 2xx outbound HTTP spans
   include request/response bodies (`input.value` / `output.value`). Default
   is on; when off, non-2xx responses must still capture full payload details.
6. Do **not** emit a `*.api.call` INFO log on success. Only emit
   `*.api.error` on non-2xx with status, elapsed_ms, and a 500-char snippet.
7. Trello URLs must always be passed through `_redact_url()` before any
   log/span URL attribute.
8. Jira clients must never log or attach the `Authorization` header to spans.
9. Do not add local duplicated span helper functions (`_current_span`,
   `_enrich_span`, `_mark_span_error`) in provider clients.

## Agent Runtime Events Catalog
| Module | Required Events |
|--------|------------------|
| `agents/factory.py` | `agents.model_client.created` (INFO with `{provider, model_name}` — never the API key); `EXCEPTION` on import / construction failure |
| `agents/team_builder.py` | `agents.team.built` (INFO with `{team_type, agent_count, selector_used}`) |
| `agents/runtime.py` | Cache hit/miss at `DEBUG`; `agents.team.started`/`agents.team.cancelled` at `INFO` |
| `agents/integrations/extractor.py` | `agents.extraction.started`/`agents.extraction.completed` with `elapsed_ms`; `agents.extraction.parse_failed` via `logger.exception` with sanitized response snippet; LLM call wrapped in `traced_block("agents.extraction.run", ...)` with canonical `input.value`/`output.value` attributes. |
| `core/tracing.py` (AutoGen event bridge) | One span per item in the event `content` array for tool events. **`mcp.tool.request <name>`** (ToolCallRequestEvent — logged at DEBUG) — attributes: `input.value` (Langfuse Input panel), `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.arguments` (payload). **`mcp.tool.result <name>`** (ToolCallExecutionEvent — logged at DEBUG) — attributes: `output.value` (Langfuse Output panel), `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.result` (payload), `gen_ai.tool.is_error`; span `StatusCode.ERROR` when `is_error=true`. All spans carry `autogen.agent.id` from the event `source` field. Other events produce `autogen.event.<type>` spans with `input.value`/`output.value` payloads. Never log or set span attributes for `args`, `env`, or `headers` of MCP server entries. The bridge handler and event loggers operate at DEBUG level so tool events (which AutoGen emits at DEBUG) are captured. |

## Request ID Propagation
1. `server/middleware.py` defines `RequestIdMiddleware` and is registered at the **top** of `MIDDLEWARE` in `config/settings.py`.
2. The middleware reads incoming `X-Request-ID` header or generates `uuid4().hex[:12]`, binds it via `bind_request_id()` (a `contextvars.ContextVar` set in `server/logging_utils.py`), echoes the value back as a response header, and clears the binding in `finally`.
3. `RequestIdFilter` (in `server/logging_utils.py`) injects the current request id onto every `LogRecord` as `record.request_id`. Default value is `"-"` when no request is active.
4. Async tasks awaited within a request inherit the contextvar automatically. Do not pass request ids manually.

## SSE Streaming Views — Mandatory request_id + trace_id Pattern

Django SSE views return a `StreamingHttpResponse` synchronously. Middleware
`finally` blocks (including OTel context teardown and `_request_id` clear)
run **before** the ASGI server begins iterating the generator body. Without
special handling, every log line inside the generator shows
`request_id: "-"` and `trace_id: "-"`.

### Required pattern for every SSE view that logs inside the generator body

```python
# 1. Capture request_id BEFORE event_stream() is defined.
_captured_request_id = get_request_id()

# 2. Create a fresh root OTel span for this run (empty context → new trace_id
#    per call) and store its W3C traceparent in Redis for reattachment.
from core.tracing import context_from_traceparent, start_root_span
from agents.session_coordination import clear_run_traceparent, store_run_traceparent

_run_span, _run_traceparent = start_root_span(
    "agents.session.run", {"session_id": session_id}
)
if _run_traceparent:
    await asyncio.to_thread(store_run_traceparent, session_id, _run_traceparent)

async def event_stream():
    # 3. Re-bind request_id (middleware cleared it before the body is consumed).
    _rid_token = bind_request_id(_captured_request_id)

    # 4. Reattach the root OTel span so all agents.* spans and log lines share
    #    the same trace_id.  Two attach tokens are used in LIFO order on exit.
    _otel_parent_token = None
    _otel_span_token = None
    if _run_span is not None and _run_traceparent:
        try:
            from opentelemetry import context as otel_context, trace
            _parent_ctx = context_from_traceparent(_run_traceparent)
            if _parent_ctx is not None:
                _otel_parent_token = otel_context.attach(_parent_ctx)
            _otel_span_token = otel_context.attach(
                trace.set_span_in_context(_run_span)
            )
        except Exception:  # noqa: BLE001
            pass

    try:
        ...  # actual streaming logic
    finally:
        # 5. Detach OTel tokens (LIFO), end the span, clear Redis key,
        #    restore request_id.
        if _otel_span_token is not None:
            try:
                from opentelemetry import context as otel_context
                otel_context.detach(_otel_span_token)
            except Exception:  # noqa: BLE001
                pass
        if _otel_parent_token is not None:
            try:
                from opentelemetry import context as otel_context
                otel_context.detach(_otel_parent_token)
            except Exception:  # noqa: BLE001
                pass
        if _run_span is not None:
            try:
                _run_span.end()
            except Exception:  # noqa: BLE001
                pass
        try:
            await asyncio.to_thread(clear_run_traceparent, session_id)
        except Exception:  # noqa: BLE001
            pass
        clear_request_id(_rid_token)
```

Key rules:
- `start_root_span` uses `Context()` (empty) — guarantees a **fresh
  trace_id** per call, not a child of the Django request span.
- Redis storage is best-effort (silent on Redis failure). When tracing is
  disabled or Redis is down, `_run_span` and `_run_traceparent` are `None`;
  all guards skip and the run continues normally with `trace_id: "-"`.
- Detach tokens **must** be applied in reverse attach order.
- `_run_span.end()` must be called only once, in `finally`.
- `clear_run_traceparent` cleans the Redis key (TTL also expires it
  automatically on process death so no manual cleanup is strictly needed).

Full details: `docs/observability.md` § "Session Run Tracing (SSE event_stream)".

## Tracing Architecture
1. `core/tracing.py` owns all OpenTelemetry wiring. Integration clients should use `core/http_tracing.py` (`instrument_http_response`) rather than importing OpenTelemetry directly.
2. `init_tracing()` runs exactly once from `server/apps.py` `ServerConfig.ready()`. Idempotent across reloads. Never raises on tracing-setup failure — logs `EXCEPTION` and continues.
3. Auto-instrumentation packages — `opentelemetry-instrumentation-django`, `-requests`, `-pymongo` — are wired inside `init_tracing()` via `_wire_auto_instrumentation()`. Each call is wrapped in try/except → `tracing.instrument_failed` exception log. Each package is gated by an env-var category toggle (see below).

## Instrumentation Category Toggles
Each span-producing layer is gated by an env var so operators can dial verbosity per concern without code changes. All toggles accept `1`/`true`/`yes`/`on` and `0`/`false`/`no`/`off`. Active values are echoed on the `tracing.enabled` startup log line (`instrument_http`, `instrument_pymongo`, `instrument_agents`).

| Category | Env var | Default | Source |
|---|---|---|---|
| HTTP / API (Django + outbound `requests`) | `OTEL_INSTRUMENT_HTTP` | `on` | `_http_tracing_enabled()` in `core/tracing.py` — gates `DjangoInstrumentor` + `RequestsInstrumentor`. |
| Database (pymongo) | `OTEL_INSTRUMENT_MONGO` | `off` | `_pymongo_tracing_enabled()` — one span per Mongo command, off by default to keep trace volume sane. |
| LLM / Agents (AutoGen event bridge) | `OTEL_INSTRUMENT_AGENTS` | `on` | `_agents_tracing_enabled()` — gates `_install_autogen_event_bridge()`. When off, the bridge never attaches and LLM/tool spans are not produced. |
| Service mutations (`@traced_function`) | *(always on)* | n/a | Explicit per-callsite spans — cheap, namespaced, never gated. |

Rules for new I/O code:
1. Do **not** add a new env-var toggle for a new manual span. Use `@traced_function("<layer>.<area>.<op>")` and let the always-on category cover it.
2. Do **not** add a fourth auto-instrumentation package without also adding a category env var following the `_flag_enabled(name, default=...)` pattern in `core/tracing.py`.
3. Never assume a span exists — callers can disable the AGENTS bridge or HTTP layer. Code that reads `trace.get_current_span()` must tolerate the no-recording sentinel.

4. AutoGen event-log → span bridge: `AutoGenEventSpanBridgeHandler` converts records on `autogen_*.events` loggers into spans with canonical `input.value`/`output.value` attributes. Both loggers are set to **DEBUG** so that `ToolCallRequestEvent` and `ToolCallExecutionEvent` (logged at DEBUG by `autogen_agentchat`) are captured alongside INFO-level `LLMCallEvent` records. The bridge owns those loggers — it strips the shared `console` handler and never mutates handler levels (mutating the shared handler would silence other namespaces). `propagate=False` keeps bridge-only DEBUG records off the root logger / console.

## Pluggable OTLP Backend
Langfuse is the currently-wired backend, but the architecture supports any
OTLP target.

1. Backend-specific construction lives in `_build_langfuse_exporter()`. It returns either an `OTLPSpanExporter` or `None` (when env is missing).
2. `init_tracing()` calls `_build_exporter()` (a single dispatch point) which delegates to `_build_langfuse_exporter()` today.
3. To swap backends:
   - Add a new helper such as `_build_generic_otlp_exporter()` that reads `OTEL_EXPORTER_OTLP_ENDPOINT` + `OTEL_EXPORTER_OTLP_HEADERS`.
   - Update `_build_exporter()` to dispatch based on env (e.g. prefer the new helper when its env vars are present).
   - No call sites in `server/` change.
4. To disable tracing entirely: omit `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`. The system logs `tracing.disabled` once and every helper (`traced_block`, `traced_function`, `set_payload_attribute`) becomes a safe no-op.

## Manual Span Patterns
- Service-layer functions: decorate with `@traced_function("service.<area>.<op>")`. The decorator wraps the call in a span and records exceptions automatically.
- One-off blocks (LLM calls, multi-step orchestration): use `with traced_block("agents.extraction.run", attributes) as span:` and set additional attributes inside the block.
- Setting payloads: always `set_payload_attribute(span, "input.value", payload)` / `set_payload_attribute(span, "output.value", payload)`. Never raw `span.set_attribute("input.value", ...)`.

## Truncation Contract
- Every payload reaching `set_payload_attribute()` is capped at `OTEL_MAX_PAYLOAD_BYTES` bytes (default `32768` — matches OTel's default attribute-value-length limit).
- When truncation happens, the span carries `span.body.truncated=true` and `span.body.original_bytes=<n>` automatically.
- Raising `OTEL_MAX_PAYLOAD_BYTES` above 32 KB also requires raising the OTel SDK env var `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, otherwise the SDK re-trims the value.
- Do not set raw bodies on span attributes outside `set_payload_attribute()`. The helper is the single enforcement point.

## Trace Correlation On Console
- `TraceContextFilter` in `server/logging_utils.py` injects `trace_id` + `span_id` onto every `LogRecord`. The JSON formatter always emits these fields (`-` when no span is active). This is the canonical way to cross-reference a console JSON line with the OTLP backend.
- `OTEL_CONSOLE_EXPORTER` controls whether the OTel SDK also dumps span objects to stderr:
  - `off` (default): backend gets all spans; console gets logs only.
  - `error`: backend gets all spans; console additionally dumps any span whose status is `ERROR` (with recorded exception, full attributes, stacktrace). Recommended for staging/production diagnostics.
  - `all` / `true` / `1`: console dumps every span (very noisy — dev-only). `LOG_LEVEL=DEBUG` implicitly upgrades the default to `all`.
- Wiring lives only in `_build_console_span_processor()` (`core/tracing.py`). Never add a second `ConsoleSpanExporter` elsewhere.
- `_install_autogen_event_bridge()` must NOT mutate the shared `console` handler's level — many loggers point at the same handler instance. Instead it strips non-bridge handlers from `autogen_*.events` loggers.

## Span Payload Contract (canonical, non-duplicated)
Do:
- Use `set_payload_attribute(span, "input.value", payload)` and `set_payload_attribute(span, "output.value", payload)` — MIME type is inferred and set automatically.
- Keep one raw AutoGen event blob under `langfuse.observation.metadata.autogen_event_raw` for debugging.

Don't:
- Duplicate the same payload under both canonical and legacy keys (`input.value` + `gen_ai.input`).
- Hardcode a single MIME type for all payloads.
- Set bodies directly without going through `set_payload_attribute()` (skips redaction + truncation).
- Emit AutoGen payload logs (any level) to console; those loggers must remain bridge-only (`propagate=False`).

## Validation Checklist
1. `grep -RnE "print\(" server/ agents/` returns zero matches (excluding tests / docs).
2. `grep -RnE "logger\.info\(\"(trello|jira)\.api\.call\"" server/` returns zero matches (per-call HTTP success INFO is now console-suppressed and replaced by spans).
3. `grep -Rn "AgentLlmFilteringExporter\|_is_agent_llm_span" agents/ server/` returns zero matches.
4. Every new I/O function emits at least one ERROR/EXCEPTION event on failure and is wrapped in a span (manual via `traced_function` or auto-instrumented via the gated HTTP/Mongo categories).
5. `grep -nE "API_KEY|SECRET|password|Authorization|Bearer" <git-diff-files>` shows no occurrences inside log message strings.
6. `python manage.py runserver` starts cleanly; the first HTTP request emits a JSON log line containing `request_id`. With `LANGFUSE_*` set, console shows `tracing.enabled` (with `max_payload_bytes`); without, shows `tracing.disabled`.
7. New modules use `logging.getLogger(__name__)` — no string literals.
8. Console output does not include `autogen_core.events` or `autogen_agentchat.events` records of any level — those loggers are bridge-only (`propagate=False`). Payload events (LLM calls, prompt dumps, tool request/execution results) appear as spans; ERROR records set the active span's status to ERROR (visible on console only when `OTEL_CONSOLE_EXPORTER=error|all`).
9. Spans use canonical payload fields only (`input.value`/`output.value` + inferred MIME types) with no duplicate payload copies, all routed through `set_payload_attribute()`.
10. Sending a request body > 32 KB produces a span with `span.body.truncated=true` and `span.body.original_bytes=<n>`. Setting `OTEL_MAX_PAYLOAD_BYTES=131072` and replaying preserves the full body.
