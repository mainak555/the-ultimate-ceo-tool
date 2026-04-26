# Observability

OpenTelemetry is the project-wide observability standard. Every layer (HTTP
clients, services, views, AutoGen agents, MongoDB) emits spans linked into a
single trace per request via the propagated `X-Request-ID` header. Logs and
traces serve different purposes and are intentionally split:

- **Logs** (stderr JSON, `LOG_LEVEL` env): high-signal lifecycle events,
  function-entry markers, and errors. Optimized for human reading.
- **Traces** (OTLP exporter — Langfuse today): full request/response
  payloads (redacted, truncated), call graph, timing. Optimized for tool
  consumption.

> See `.agents/skills/observability_logging/SKILL.md` for the per-PR contract
> contributors must follow when adding new I/O code.

---

## Architecture

```
┌────────────────────────┐       ┌─────────────────────────┐
│  Django request        │──┬──▶ │  console (stderr JSON)  │
│  RequestIdMiddleware   │  │    │  request_id+trace_id    │
└────────────────────────┘  │    └─────────────────────────┘
        │                    │
        ▼                    │
┌────────────────────────┐  │
│  TracerProvider        │  │
│   (core/tracing.py)    │  │
└────────────────────────┘  │
        │                    │
        ├── BatchSpanProcessor ─▶ OTLP exporter (Langfuse | other)
        └── SimpleSpanProcessor ▶ ConsoleSpanExporter (opt-in)
```

- `init_tracing()` runs exactly once from `server/apps.py`
  `ServerConfig.ready()`. Idempotent. Never raises.
- Auto-instrumentation: `opentelemetry-instrumentation-django`,
  `-requests`, `-pymongo`. Wired inside `_wire_auto_instrumentation()`,
  each category gated by an env var (see
  [Instrumentation Categories](#instrumentation-categories) below).
- AutoGen event-log → span bridge: `AutoGenEventSpanBridgeHandler` converts
  INFO records on `autogen_core.events` / `autogen_agentchat.events` into
  spans with canonical `input.value` / `output.value` payloads. Gated by
  `OTEL_INSTRUMENT_AGENTS` (default on).

---

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `LOG_LEVEL` | Console log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). `DEBUG` implicitly upgrades `OTEL_CONSOLE_EXPORTER` to `all`. | `INFO` |
| `LANGFUSE_PUBLIC_KEY` | Enables tracing when set | *(unset → tracing disabled)* |
| `LANGFUSE_SECRET_KEY` | Enables tracing when set | *(unset → tracing disabled)* |
| `LANGFUSE_HOST` | Langfuse OTLP endpoint host | `https://cloud.langfuse.com` |
| `OTEL_SERVICE_NAME` | Service name attached to all spans | `product-discovery` |
| `OTEL_MAX_PAYLOAD_BYTES` | Per-attribute payload truncation cap (bytes) | `32768` |
| `OTEL_CONSOLE_EXPORTER` | Console span output mode (`off` / `error` / `all`) | `off` |
| `OTEL_INSTRUMENT_HTTP` | Django + outbound `requests` auto-instrumentation. Disable when only deeper layers matter. | `on` |
| `OTEL_HTTP_LOG_BODY` | Controls request/response payload capture for successful (2xx) outbound HTTP calls instrumented via `core/http_tracing.py`. Non-2xx still always capture payloads. | `on` |
| `OTEL_INSTRUMENT_MONGO` | pymongo auto-instrumentation (one span per Mongo command — high cardinality). Enable for query/connection diagnostics. | `off` |
| `OTEL_INSTRUMENT_AGENTS` | AutoGen event-log → span bridge (LLM calls, prompts, tool invocations). Disable to silence the LLM/agent layer entirely. | `on` |
| `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` | OTel SDK attribute length limit. Must be raised when raising `OTEL_MAX_PAYLOAD_BYTES` above 32 KB. | *(SDK default 32768)* |

Missing `LANGFUSE_*` keys → `tracing.disabled` info log, no exporter, no
errors. Every helper (`traced_block`, `traced_function`,
`set_payload_attribute`) becomes a safe no-op.

The active values for all toggles are recorded on the startup
`tracing.enabled` log line (`instrument_http`, `instrument_pymongo`,
`instrument_agents`, `console_span_mode`, `max_payload_bytes`) so wiring
can be confirmed at a glance.

---

## Instrumentation Categories

Each span-producing layer is gated by an env var so operators can dial
verbosity per concern without code changes. All toggles accept
`1`/`true`/`yes`/`on` and `0`/`false`/`no`/`off` (case-insensitive).

| Category | Env var | Default | What it produces | When to flip |
|---|---|---|---|---|
| **HTTP / API** (Django + outbound `requests`) | `OTEL_INSTRUMENT_HTTP` | `on` | One span per Django request; one span per outbound HTTP call (Trello, Jira, Langfuse export, etc.) | Disable in narrow load tests where you only care about agent latency. |
| **Database** (pymongo) | `OTEL_INSTRUMENT_MONGO` | `off` | One span per Mongo command (`find`, `update_one`, etc.) | Enable when diagnosing slow queries, connection storms, or unexpected reads. Off by default because trace volume balloons quickly. |
| **LLM / Agents** (AutoGen event bridge) | `OTEL_INSTRUMENT_AGENTS` | `on` | `autogen.event.LLMCall`, `autogen.event.ToolCall`, prompts, model responses, token usage | Disable when running purely-deterministic flows (Trello/Jira pushes without an agent run) and you want to suppress LLM payloads from the OTLP backend. |
| **Service mutations** (manual `@traced_function`) | *(none — always on)* | n/a | `service.project.create`, `service.chat.create`, `service.trello.export.push`, `service.jira.<type>.push_issues`, etc. | Always emitted because each callsite is explicitly chosen by the developer; spans are cheap and namespaced. |

### Common combinations

```bash
# Production default — quiet console, full traces to OTLP, no Mongo noise
LOG_LEVEL=INFO OTEL_CONSOLE_EXPORTER=off

# Production diagnostics — dump only failures with stacktrace
LOG_LEVEL=INFO OTEL_CONSOLE_EXPORTER=error

# Mongo deep dive
OTEL_INSTRUMENT_MONGO=1

# Reproduce an agent issue without HTTP/Mongo noise in the OTLP backend
OTEL_INSTRUMENT_HTTP=0 OTEL_INSTRUMENT_MONGO=0 OTEL_INSTRUMENT_AGENTS=1

# Local firehose — every span on console
LOG_LEVEL=DEBUG
```

---

## What Is Traced

| Layer | Mechanism | Span Examples |
|---|---|---|
| Django requests | Auto (`DjangoInstrumentor`) — gated by `OTEL_INSTRUMENT_HTTP` | `GET /chat/sessions/<id>/` |
| Outbound HTTP (Trello, Jira, future providers) | Auto (`RequestsInstrumentor`) + shared `core/http_tracing.py` helper (`instrument_http_response`) in client `_handle_api_response()`/fallback branches — gated by `OTEL_INSTRUMENT_HTTP` | `HTTP POST` with `<provider>.action`, `http.url` (redacted when needed), `http.status_code`, `input.value`, `output.value`, and `<provider>.error.*` on non-2xx |
| MongoDB ops | Auto (`PymongoInstrumentor`) — gated by `OTEL_INSTRUMENT_MONGO` (default off) | `mongo.find`, `mongo.insert_one` |
| Service-layer mutations | Manual `@traced_function` (always on) | `service.project.create`, `service.chat.create`, `service.trello.export.push`, `service.jira.<type>.push_issues` |
| AutoGen agent runs | Event-log bridge — gated by `OTEL_INSTRUMENT_AGENTS` | `autogen.event.LLMCall`, `autogen.event.ToolCall` with full payloads |

### Trace Hierarchy

A single user action produces one trace, with spans nested by call stack so
parent/child timing in Langfuse mirrors what actually happened. Typical chat
turn that triggers an MCP-enabled agent:

```
Django request span                              [auto · OTEL_INSTRUMENT_HTTP]
└─ @traced_function on view / service entry      [always on]
   └─ service.* mutation                         [always on]
      └─ AutoGen agent run                       [bridge · OTEL_INSTRUMENT_AGENTS]
         ├─ chat <model>            (LLM call)   [autogen-core]
         │  └─ HTTP POST <provider> (LLM API)    [auto · OTEL_INSTRUMENT_HTTP]
         └─ execute_tool <name>     (MCP tool)   [autogen-core]
            └─ HTTP POST <mcp-gateway>           [auto · only for streamable HTTP transport]
                                                  (stdio MCP runs in a child process — no HTTP span)

Sibling spans on the same trace:
└─ mongo.<op>                                    [auto · OTEL_INSTRUMENT_MONGO, off by default]
└─ HTTP <verb> <trello|jira>                     [auto + core/http_tracing.py]
```

Context flow:

- The `X-Request-ID` header (or one we generate in `RequestIdMiddleware`) is
  attached to every log line and forwarded on outbound HTTP, tying logs and
  spans across hops.
- AutoGen calls `trace.get_tracer("autogen-core")` (no explicit provider), so
  it picks up our globally installed TracerProvider automatically — no extra
  wiring is needed for LLM, tool, or MCP `execute_tool` spans to nest under
  the calling agent's run.

---

## Console Behavior

### Logs
- JSON-formatted stderr via `python-json-logger`.
- Every record carries `timestamp`, `level`, `logger`, `message`,
  `request_id`, `trace_id`, `span_id`. Outside a span, `trace_id` /
  `span_id` are `"-"`.
- Filters on the `console` handler:
  - `RequestIdFilter` — injects `request_id` from the `contextvars` set by `RequestIdMiddleware`.
  - `TraceContextFilter` — reads the active span and injects `trace_id` / `span_id`.
  - `EventOnlyConsoleFilter` — drops INFO records ending in `.api.call`. Per-call HTTP success detail lives on spans, not console.
- AutoGen payload loggers (`autogen_core.events`, `autogen_agentchat.events`) are **owned by `core.tracing._install_autogen_event_bridge()`**, not by `config/settings.py` LOGGING. The bridge strips the shared `console` handler from those loggers and attaches `AutoGenEventSpanBridgeHandler` instead, so INFO payload events flow to spans (with redaction + truncation) and never reach console. ERROR records on those loggers set `Span.status = ERROR`, which is then surfaced through `OTEL_CONSOLE_EXPORTER=error` (if enabled) and through Django request logging at the outer layer.

### Span dumps (opt-in)

Set `OTEL_CONSOLE_EXPORTER` for stderr span output:

| Mode | Behavior | Use case |
|---|---|---|
| `off` (default) | No spans on console. OTLP backend still receives all spans. | Production unless debugging |
| `error` | Print only `StatusCode.ERROR` spans (full attributes + recorded `exception.type` / `message` / `stacktrace`). | Recommended for staging / production diagnostics |
| `all` / `true` / `1` | Print every finished span. Very noisy. | Dev-only deep dives |

`LOG_LEVEL=DEBUG` implicitly upgrades the default to `all`.

Wiring lives only in `_build_console_span_processor()` in
`core/tracing.py`. Never add a second `ConsoleSpanExporter` elsewhere.

---

## Pluggable OTLP Backend

Langfuse is the currently-wired exporter, but the architecture supports any
OTLP target.

- Backend-specific construction lives in `_build_langfuse_exporter()` in
  `core/tracing.py`. Returns either an `OTLPSpanExporter` or `None`.
- `_build_exporter()` is the single dispatch point.

### Swapping backends (Jaeger / Tempo / Honeycomb / OTel collector)

```python
def _build_generic_otlp_exporter():
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return None
    headers = _parse_otlp_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""))
    return OTLPSpanExporter(endpoint=endpoint, headers=headers)

def _build_exporter():
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return _build_generic_otlp_exporter()
    return _build_langfuse_exporter()
```

No call sites in `server/` change. Service decorators and
`set_payload_attribute()` remain identical; outbound client instrumentation
is centralized in `core/http_tracing.py`.

---

## Shared HTTP Client Tracing Standard

All outbound integration clients must use `core/http_tracing.py` as the
single tracing path:

- `instrument_http_response(resp, provider=..., action=...)` on success.
- `instrument_http_response(..., detail=..., error_messages=..., field_errors=...)`
  on non-2xx paths (including graceful fallback branches that continue).
- Name client response handlers meaningfully (for example
  `_handle_api_response`) instead of generic names like `_check`.

This guarantees a uniform contract across all providers:

- Request/response payloads always go to canonical `input.value` /
  `output.value` via `set_payload_attribute()`.
- `OTEL_HTTP_LOG_BODY=off` skips `input.value` / `output.value`
  for successful (2xx) outbound calls to reduce payload volume.
- Error spans always set `StatusCode.ERROR` with `<provider>.error.detail`,
  `<provider>.error.status_code`, `<provider>.error.response_body`, and
  optional structured payloads (`<provider>.error.messages`,
  `<provider>.error.fields`).
- Non-2xx spans always include request/response payloads regardless of
  `OTEL_HTTP_LOG_BODY`.
- Provider-specific code only supplies business parsing and optional URL
  redaction; it does not implement span plumbing.

---

## Span Payload Contract

**Canonical, non-duplicated.** Every payload reaching a span goes through
`set_payload_attribute(span, key, value)` from `core/tracing.py`, which:

1. Calls `redact_payload()` — masks any field whose key matches
   `(?i)api_key|secret|password|token|authorization|x-app-secret-key`.
2. Calls `truncate_for_span()` — caps at `OTEL_MAX_PAYLOAD_BYTES` (default
   32 KB).
3. Sets the attribute and the matching `<prefix>.mime_type`
   (`application/json`, `text/markdown`, `text/plain`).
4. On truncation, sets `span.body.truncated=true` and
   `span.body.original_bytes=<n>`.

**Do:**
- `set_payload_attribute(span, "input.value", payload)`
- `set_payload_attribute(span, "output.value", payload)`
- Keep one raw AutoGen event blob under
  `langfuse.observation.metadata.autogen_event_raw` for debugging.

**Don't:**
- Duplicate the same payload under both canonical and legacy keys
  (`input.value` + `gen_ai.input`).
- Hardcode a single MIME type for all payloads.
- Set bodies directly with `span.set_attribute("input.value", ...)` —
  skips redaction + truncation.

### Raising the truncation cap

`OTEL_MAX_PAYLOAD_BYTES` defaults to 32768 to match the OTel SDK's default
attribute-value-length limit. Raising the cap above 32 KB also requires
raising `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, otherwise the SDK re-trims the
value before export.

---

## Request ID Propagation

1. `server/middleware.py` `RequestIdMiddleware` runs at the **top** of
   `MIDDLEWARE`, reads the incoming `X-Request-ID` header (or generates
   `uuid4().hex[:12]`), binds it via `bind_request_id()` (a
   `contextvars.ContextVar` set in `server/logging_utils.py`), echoes the
   value back as a response header, and clears the binding in `finally`.
2. `RequestIdFilter` injects the current id onto every `LogRecord` as
   `record.request_id`. Default is `"-"` when no request is active.
3. Async tasks awaited within a request inherit the contextvar
   automatically. Do not pass request ids manually.

---

## Logger & Event Naming

- Every Python module that performs I/O declares
  `logger = logging.getLogger(__name__)` at module top.
- Event names use dotted snake_case scoped by layer.

| Layer | Examples |
|---|---|
| Tracing lifecycle | `tracing.enabled`, `tracing.disabled`, `tracing.instrument_failed`, `tracing.setup_failed` |
| MongoDB | `mongo.connect`, `mongo.connect_failed` |
| Services | `project.created`, `project.updated`, `project.deleted`, `chat.session.started`, `chat.session.ended` |
| HTTP errors | `trello.api.error`, `jira.api.error` (success no longer logged — lives on spans) |
| Agent runtime | `agents.model_client.created`, `agents.team.built`, `agents.team.cancelled`, `agents.extraction.completed`, `agents.extraction.parse_failed`, `agents.mcp.created`, `agents.mcp.closed`, `agents.mcp.failed` |

Levels:
- `INFO` — successful lifecycle events with structured `extra={...}`.
- `WARNING` — immediately before raising a `ValueError` for an expected
  business-rule failure.
- `EXCEPTION` — unexpected exceptions (full traceback).

---

## Redaction Guarantees

1. Trello `key=` / `token=` query params are stripped via `_redact_url()` in
   `server/trello_client.py` before any log/span attribute.
2. `Authorization` headers are never logged or attached to spans.
3. `set_payload_attribute()` calls `redact_payload()` recursively on the
   value before serialization.
4. Body snippets in error logs are capped at 500 characters and sanitized.

> ⚠️ Trello card descriptions and Jira issue summaries are forwarded to the
> configured OTLP backend as span payloads. Treat the backend as
> PII-bearing.

---

## Adding a New Span

```python
from core.tracing import traced_function, traced_block, set_payload_attribute

# 1. Decorator on a service-layer function
@traced_function("service.<area>.<op>")
def my_service_op(...):
    ...

# 2. Manual block (e.g. multi-step orchestration)
with traced_block("agents.extraction.run", {"agent": "extractor"}) as span:
    response = call_llm(prompt)
    set_payload_attribute(span, "input.value", prompt)
    set_payload_attribute(span, "output.value", response)
```

When tracing is disabled, both helpers are safe no-ops — never feature-flag
your span code at the call site.

---

## Validation Checklist

Before merging new I/O code, confirm:

1. `grep -RnE "print\(" server/ agents/` returns zero matches (excluding tests / docs).
2. `grep -RnE "logger\.info\(\"(trello|jira)\.api\.call\"" server/` returns zero matches.
3. `grep -Rn "AgentLlmFilteringExporter\|_is_agent_llm_span" agents/ server/` returns zero matches.
4. Every new I/O function emits at least one ERROR/EXCEPTION event on failure and is wrapped in a span (manual via `traced_function` or auto-instrumented).
5. `grep -nE "API_KEY|SECRET|password|Authorization|Bearer" <git-diff-files>` shows no occurrences inside log message strings.
6. `python manage.py runserver` starts cleanly; first HTTP request emits a JSON log line containing `request_id`, `trace_id`, `span_id`. With `LANGFUSE_*` set, console shows `tracing.enabled` (with `max_payload_bytes` and `console_span_mode`); without, shows `tracing.disabled`.
7. Spans use canonical payload fields only (`input.value` / `output.value` + inferred MIME types) routed through `set_payload_attribute()`.
8. Sending a request body > 32 KB produces a span with `span.body.truncated=true` and `span.body.original_bytes=<n>`.
9. With `OTEL_CONSOLE_EXPORTER=error`, an intentionally raised exception inside a `traced_block` dumps a single-span JSON blob to stderr with `status_code: "ERROR"`, `exception.stacktrace`, and matching `trace_id` from the preceding log line.
10. New HTTP clients do not implement local span helper copies (for example `_current_span`, `_enrich_span`, `_mark_span_error`) and instead call `core.http_tracing.instrument_http_response()`.
