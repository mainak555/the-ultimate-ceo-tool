# Product Discovery

Agent-based product roadmap planning tool — OKR to Product Backlog.  
Django SPA with HTMX, SCSS, and MongoDB (PyMongo).

---

## Quick Start (Local — .venv)

```bash
# 1. Create and activate virtual environment
python -m venv .venv

# Windows PowerShell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
#    Edit .env with your MongoDB URI and admin secret key

# 4. Run the development server (ASGI — required for SSE streaming)
uvicorn config.asgi:application --reload --port 8000
```

Open **http://127.0.0.1:8000** in your browser.

> `python manage.py runserver` also works but is single-threaded; SSE runs will block all other requests while an agent is executing.

---

## Docker

```bash
# Build
docker build -t product-discovery .

# Run (uses .env file for configuration)
docker run -p 8000:8000 --env-file .env product-discovery
```

The container runs `uvicorn` (ASGI) by default, which is required for SSE streaming.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `APP_SECRET_KEY` | Admin password for write access | *(required)* |
| `MONGODB_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGODB_NAME` | MongoDB database name | `product_discovery` |
| `DEBUG` | Django debug mode | `True` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `LOG_LEVEL` | Console log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). `DEBUG` upgrades `OTEL_CONSOLE_EXPORTER` default to `all`. | `INFO` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key — enables tracing when set | *(unset → tracing disabled)* |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key — enables tracing when set | *(unset → tracing disabled)* |
| `LANGFUSE_HOST` | Langfuse OTLP endpoint host | `https://cloud.langfuse.com` |
| `OTEL_SERVICE_NAME` | Service name attached to all spans | `product-discovery` |
| `OTEL_MAX_PAYLOAD_BYTES` | Per-attribute span payload truncation cap (bytes) | `32768` |
| `OTEL_CONSOLE_EXPORTER` | Console span output mode (`off` / `error` / `all`). See [docs/observability.md](docs/observability.md). | `off` |
| `OTEL_INSTRUMENT_HTTP` | Django + outbound `requests` auto-instrumentation (app/API spans). | `on` |
| `OTEL_INSTRUMENT_PYMONGO` | Pymongo auto-instrumentation (one span per Mongo command — high cardinality). Enable for query/connection diagnostics. | `off` |
| `OTEL_INSTRUMENT_AGENTS` | AutoGen event-log → span bridge (LLM calls, prompts, tool spans). | `on` |
| `OPENAI_API_KEY` | API key for direct OpenAI models | *(required for `openai` models)* |
| `OPENAI_API_URL` | Endpoint fallback for `openai` models when `endpoint` is omitted in `agent_models.json` | *(optional)* |
| `ANTHROPIC_API_KEY` | API key for direct Anthropic models | *(required for `anthropic` models)* |
| `ANTHROPIC_API_URL` | Endpoint fallback for `anthropic` models when `endpoint` is omitted in `agent_models.json` | *(optional)* |
| `GOOGLE_API_KEY` | API key for direct Google Gemini models | *(required for `google` models)* |
| `GOOGLE_API_URL` | Endpoint fallback for `google` models when `endpoint` is omitted in `agent_models.json` | *(optional)* |
| `AZURE_OPENAI_API_KEY` | API key for Azure AI Foundry OpenAI deployments | *(required for `azure_openai` models)* |
| `AZURE_OPENAI_API_URL` | Endpoint fallback for `azure_openai` models when `endpoint` is omitted in `agent_models.json` | *(required if JSON `endpoint` is missing)* |
| `AZURE_ANTHROPIC_API_KEY` | API key for Azure AI Foundry Anthropic deployments | *(required for `azure_anthropic` models)* |
| `AZURE_ANTHROPIC_API_URL` | Endpoint fallback for `azure_anthropic` models when `endpoint` is omitted in `agent_models.json` | *(required if JSON `endpoint` is missing)* |

---

## Observability

OpenTelemetry is the project-wide observability standard. Every layer (Django
requests, outbound HTTP to Trello/Jira, MongoDB, service mutations, AutoGen
agent runs) emits spans linked into a single trace per request via the
propagated `X-Request-ID` header. Logs and traces are intentionally split:

- **Logs** — JSON to stderr, every line carries `request_id`, `trace_id`, `span_id`. Lifecycle events + `WARNING`+ only; per-call HTTP success detail lives on spans.
- **Traces** — full request/response payloads (redacted, truncated at 32 KB) shipped to the configured OTLP backend (Langfuse today; pluggable). `OTEL_CONSOLE_EXPORTER=error` additionally dumps any failed span to stderr with its full attribute set + stacktrace.

### Two console dials

`LOG_LEVEL` and `OTEL_CONSOLE_EXPORTER` are **independent** — one controls log
records, the other controls span dumps. Combine them for the verbosity you
want:

| Goal | `LOG_LEVEL` | `OTEL_CONSOLE_EXPORTER` |
|------|-------------|-------------------------|
| Quiet production console (default) | `INFO` | `off` |
| Surface failures with full stacktrace, no success noise | `INFO` | `error` |
| Local dev firehose (every span dumped) | `DEBUG` | *(auto-upgrades to `all`)* |
| CI / staging sanity | `INFO` | `error` |

**`LOG_LEVEL`** — sets verbosity of `logger.info(...)` / `logger.error(...)`
calls in `server.*` and `agents.*`. Accepts `DEBUG` / `INFO` / `WARNING` /
`ERROR` / `CRITICAL`. Per-HTTP-call success records are dropped from console
even at `INFO` (they live on spans); lifecycle events like `project.created`,
`tracing.enabled`, `mongo.connect` always render at `INFO`. `DEBUG` also
upgrades the default `OTEL_CONSOLE_EXPORTER` to `all`. Has **no** effect on
`autogen_core.events` / `autogen_agentchat.events` — those loggers are owned
by the span bridge and never reach console.

**`OTEL_CONSOLE_EXPORTER`** — controls whether finished OpenTelemetry spans
are also written to stderr (they always go to the OTLP backend regardless):

| Mode | Behavior | When to use |
|------|----------|-------------|
| `off` | No spans on console. | Production default. |
| `error` | Only spans with status `ERROR`, dumped with full attributes + `exception.stacktrace`. | Diagnose failures without round-tripping to Langfuse. |
| `all` | Every span dumped. Very verbose. | Local debugging. |

The active mode is recorded on the startup `tracing.enabled` log line as
`console_span_mode`, so you can confirm the wiring.

### Instrumentation categories (per-layer toggles)

Each span-producing layer has its own env-var switch. All accept
`1`/`true`/`yes`/`on` and `0`/`false`/`no`/`off`. Active values are echoed
on the `tracing.enabled` startup log line.

| Category | Env var | Default | Produces |
|---|---|---|---|
| HTTP / API (Django + `requests`) | `OTEL_INSTRUMENT_HTTP` | `on` | One span per Django request and per outbound HTTP call (Trello, Jira). |
| Database (pymongo) | `OTEL_INSTRUMENT_PYMONGO` | `off` | One span per Mongo command. Off by default — enable for DB diagnostics. |
| LLM / Agents (AutoGen bridge) | `OTEL_INSTRUMENT_AGENTS` | `on` | LLM calls, prompts, model responses, tool invocations, token usage. |
| Service mutations (`@traced_function`) | *(always on)* | n/a | Explicit service-layer spans (`service.project.create`, `service.jira.<type>.push_issues`, …). |

Useful combos:

- Mongo deep dive: `OTEL_INSTRUMENT_PYMONGO=1`
- Silence LLM payloads (e.g. PII review): `OTEL_INSTRUMENT_AGENTS=0`
- Pure-agent latency profiling, no HTTP/Mongo noise: `OTEL_INSTRUMENT_HTTP=0 OTEL_INSTRUMENT_PYMONGO=0`

Full architecture, env vars, span payload contract, redaction rules,
backend-swap pattern, and validation checklist are in
[docs/observability.md](docs/observability.md). The per-PR contract for
adding new I/O code is in
[`.agents/skills/observability_logging/SKILL.md`](.agents/skills/observability_logging/SKILL.md).

> ⚠️ Trello card descriptions and Jira issue summaries are forwarded to the configured OTLP backend as span payloads. Treat the backend as PII-bearing.

---

## Project Structure

See [AGENTS.md](AGENTS.md) for full architecture and development instructions.

## Configuration Notes

- Supported agent models are defined in `agent_models.json` at the repository root.
- The runtime resolves model endpoint as: JSON `endpoint` first, then `{PROVIDER_UPPER}_API_URL` env var fallback.
- For Azure models, the model key is the deployment name. Azure endpoints remain required, but may now come from either JSON `endpoint` or `AZURE_OPENAI_API_URL` / `AZURE_ANTHROPIC_API_URL`.
- The AutoGen runtime lives in the root `agents/` package, separate from the Django `server/` app.
- Agent execution is streamed over SSE (`/chat/sessions/<id>/run/`). The server **must** run under ASGI (`uvicorn`) for real-time streaming; WSGI will buffer the entire response before sending.
