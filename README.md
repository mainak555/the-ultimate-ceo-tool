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

The repository ships three deployment topologies under `deployments/`:

| Topology | Use when | MCP location |
| --- | --- | --- |
| [`deployments/standalone/`](deployments/standalone/README.md) | Single-container hosts (Vercel, HF Spaces, fly.io, local dev) | Node bundled in the app image; stdio MCP servers in-process |
| [`deployments/compose/`](deployments/compose/README.md) | Docker Compose / single VM | Python-only `app` + Node-based `mcp-gateway` sidecar |
| [`deployments/k8s/`](deployments/k8s/README.md) | Kubernetes (Helm) | Same sidecar split, full Helm chart |

Quick local build/run (standalone topology — Python + Node bundled):

```bash
docker build -f deployments/standalone/Dockerfile -t product-discovery .
docker run -p 8000:8000 --env-file .env product-discovery
```

The container runs `uvicorn` (ASGI) by default, which is required for SSE
streaming of agent output.

---

## MCP (Model Context Protocol) Tools

Assistant agents can be augmented with MCP tools, configured per-project and
per-agent. Each agent's `mcp_tools` field is one of:

- `none` — no tools attached.
- `shared` — uses the project-level `shared_mcp_tools` JSON.
- `dedicated` — uses a per-agent `mcp_configuration` JSON.

Both JSON fields share the same shape:

```jsonc
{
  "mcpServers": {
    "fs":    { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"] },
    "fetch": { "transport": "http", "url": "http://mcp-gateway:9000/fetch/mcp" }
  }
}
```

Supported transports: **stdio** (`{command, args, env}`) and **streamable HTTP**
(`{transport: "http", url, headers}`). SSE is intentionally unsupported.

For the standalone deployment, the app image bundles Node so stdio MCP
servers can run in-process. For compose / k8s, a separate `mcp-gateway`
container hosts MCP servers and exposes them over streamable HTTP.

**Tracing** — every MCP tool call is automatically traced as a child span of
the calling agent's run. AutoGen's `McpWorkbench` wraps each invocation in an
OpenTelemetry `execute_tool <tool_name>` span (GenAI semantic conventions:
`gen_ai.operation.name=execute_tool`, `gen_ai.system=autogen`,
`gen_ai.tool.name`, `gen_ai.tool.call.id`), and our global tracer provider
ships those spans to the configured OTLP backend (Langfuse) alongside the
Django request, agent, and LLM spans — all stitched into a single trace via
the `X-Request-ID` header. No extra wiring is required; the toggle is
`OTEL_INSTRUMENT_AGENTS` (default `on`). Server `args` / `env` / `headers`
are never logged or set as span attributes (see AGENTS rule 52).

Full documentation: [docs/mcp_integration.md](docs/mcp_integration.md).

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
| `OTEL_HTTP_LOG_BODY` | Controls request/response payload capture for successful (2xx) outbound HTTP API spans via `core/http_tracing.py`. Non-2xx payloads are always captured. | `on` |
| `OTEL_INSTRUMENT_MONGO` | Pymongo auto-instrumentation (one span per Mongo command — high cardinality). Enable for query/connection diagnostics. | `off` |
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

A single chat turn produces one trace; spans nest by call stack — Django
request → service mutation → agent run → LLM call / `execute_tool` (MCP) →
outbound HTTP. See the trace-hierarchy diagram in
[docs/observability.md#trace-hierarchy](docs/observability.md#trace-hierarchy).

Outbound integration clients (Trello, Jira, and future providers) must use the
shared helper `core/http_tracing.py` (`instrument_http_response`) so request,
response, and error-detail span attributes are emitted consistently without
duplicating provider-local span plumbing.

Set `OTEL_HTTP_LOG_BODY=off` to skip request/response payload capture
for successful outbound HTTP calls while still capturing full payload details
for non-2xx responses. This toggle applies only to integration HTTP client
span payload capture and does not affect AutoGen/LLM tracing controlled by
`OTEL_INSTRUMENT_AGENTS`.

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
| Database (pymongo) | `OTEL_INSTRUMENT_MONGO` | `off` | One span per Mongo command. Off by default — enable for DB diagnostics. |
| LLM / Agents (AutoGen bridge) | `OTEL_INSTRUMENT_AGENTS` | `on` | LLM calls, prompts, model responses, tool invocations, token usage. |
| Service mutations (`@traced_function`) | *(always on)* | n/a | Explicit service-layer spans (`service.project.create`, `service.jira.<type>.push_issues`, …). |

Useful combos:

- Mongo deep dive: `OTEL_INSTRUMENT_MONGO=1`
- Silence LLM payloads (e.g. PII review): `OTEL_INSTRUMENT_AGENTS=0`
- Pure-agent latency profiling, no HTTP/Mongo noise: `OTEL_INSTRUMENT_HTTP=0 OTEL_INSTRUMENT_MONGO=0`

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

---

## Export Schema Contract (Trello)

The Trello export popup supports project-specific extraction prompts, but the extracted JSON must always follow the same schema contract so the exporter can parse and push cards reliably.

### Why this matters

- Prompt wording can change per project and per `export_mapping.system_prompt`
- Export parsing logic is schema-based, not prose-based
- If extraction output shape drifts, save/export actions can fail or produce partial pushes

### Required extracted JSON shape

```json
[
	{
		"card_title": "string",
		"card_description": "string",
		"checklists": [
			{
				"name": "string",
				"items": [
					{
						"title": "string",
						"checked": false
					}
				]
			}
		],
		"custom_fields": [
			{
				"field_name": "string",
				"field_type": "text|number|date|checkbox|list",
				"value": "string"
			}
		],
		"labels": ["string"],
		"priority": "Low|Medium|High|Critical",
		"confidence_score": 0.0
	}
]
```

### Field contract

| Path | Type | Required | Notes |
|---|---|---|---|
| `<root>` | array | Yes | Root list of Trello card objects. |
| `[].card_title` | string | Yes | Trello card name. Empty values normalize to `Untitled`. |
| `[].card_description` | string | No | Trello card description. Defaults to empty string. |
| `[].checklists` | array | No | Checklist groups for the card. |
| `[].checklists[].name` | string | No | Checklist name. Defaults to `Tasks`. |
| `[].checklists[].items` | array | No | Checklist items. |
| `[].checklists[].items[].title` | string | Yes (when item exists) | Checklist item text. Empty titles are dropped. |
| `[].checklists[].items[].checked` | boolean | No | Defaults to `false` when omitted. |
| `[].custom_fields` | array | No | Dynamic text-only metadata fields. |
| `[].custom_fields[].field_name` | string | Yes (when field exists) | Empty names are dropped. |
| `[].custom_fields[].field_type` | string | No | Trello supports `text`, `number`, `date`, `checkbox`, `list`; current exporter normalization stores `text`. |
| `[].custom_fields[].value` | string | No | Defaults to empty string. |
| `[].labels` | array | No | Case-insensitive deduplicated labels. |
| `[].priority` | string | No | Accepted values: `Low`, `Medium`, `High`, `Critical` (case-insensitive input). |
| `[].confidence_score` | number | No | Clamped to range `0.0` to `1.0`. |

### Compatibility rules

1. Trello extraction prompt output must be a JSON array of card objects matching the schema above.
2. Export endpoint responses continue to return `{items: [...]}` where `items` contains normalized card objects.
3. Legacy extraction keys (`title`, `description`, `children`) are accepted for backward compatibility, but new prompts should emit `card_title`, `card_description`, and `checklists`.
4. Additional fields may be present, but Trello exporter behavior is defined only for the contract fields above.

For implementation details, endpoint flow, and mapping behavior, see [docs/trello_integration.md](docs/trello_integration.md). For the cross-provider documentation standard used by all export popups, see [docs/export_schema_contracts.md](docs/export_schema_contracts.md).

---

## Export Schema Contract (Jira Software)

Jira Software export supports hierarchical issue trees. The extracted or edited payload must follow a stable schema so parent linking (`temp_id` / `parent_temp_id`) and push correlation remain deterministic.

### Required extracted/edited JSON shape

```json
[
	{
		"temp_id": "T1",
		"parent_temp_id": null,
		"existing_issue_key": "",
		"summary": "string",
		"description": "string",
		"issue_type": "Epic|Feature|Story|Task|Sub-task|Bug",
		"priority": "Highest|High|Medium|Low|Lowest",
		"sprint": "",
		"labels": ["string"],
		"story_points": 5,
		"components": ["string"],
		"acceptance_criteria": "string",
		"confidence_score": 0.0
	}
]
```

### Field contract

| Path | Type | Required | Notes |
|---|---|---|---|
| `<root>` | array | Yes | Root list of Jira Software issue objects. |
| `[].temp_id` | string | Yes | Client-stable id used only for batch parent mapping. Auto-generated when missing. |
| `[].parent_temp_id` | string\|null | No | Parent item id. `null` means root. |
| `[].existing_issue_key` | string | No | Empty string means create new issue. Non-empty value means update the selected Jira issue using current card fields. |
| `[].summary` | string | Yes | Jira issue summary. Empty values normalize to `Untitled`. |
| `[].description` | string | No | Plain text; wrapped to ADF at push time. |
| `[].issue_type` | string | Yes | Intended issue type. Resolved against destination project issue-type scheme. |
| `[].priority` | string | No | Jira priority label. |
| `[].sprint` | string | No | Sprint id string. Empty string means Backlog (skip Agile sprint assignment API). |
| `[].labels` | array | No | Label strings. |
| `[].story_points` | number\|null | No | Mapped to Jira story-points custom field when available. |
| `[].components` | array | No | Jira component names. |
| `[].acceptance_criteria` | string | No | Appended into Description under an "Acceptance Criteria" section (Jira has no native acceptance-criteria field). |
| `[].confidence_score` | number | No | Clamped to `0.0`–`1.0`. |

### Compatibility rules

1. Hierarchy contract is `temp_id` + `parent_temp_id` only; do not store `depth_level`.
2. Push runs BFS and returns result rows echoing `temp_id` for correlation.
3. If `existing_issue_key` is set, push updates the selected Jira issue, maps `temp_id` to that key, and still allows descendants to link through `temp_to_key`.
4. Unknown extra fields may be present in payloads, but exporter behavior is defined only for the schema above.

For full endpoint and behavior details, see [docs/jira_integration.md](docs/jira_integration.md).
