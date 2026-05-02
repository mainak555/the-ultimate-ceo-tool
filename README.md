# The Ultimate CEO Tool
### Turn Vision into Action

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

## Manual & Reference Index

### User guides

- Product and UI flow: [docs/UI.md](docs/UI.md)
- API and endpoint reference: [docs/API.md](docs/API.md)
- Human gate + remote collaboration: [docs/human_gate_remote_users.md](docs/human_gate_remote_users.md)
- Trello export flow: [docs/trello_integration.md](docs/trello_integration.md)
- Jira export flow: [docs/jira_integration.md](docs/jira_integration.md)
- Attachment lifecycle and limits: [docs/attachment_storage.md](docs/attachment_storage.md)

### Developer guides

- Architecture and layering contracts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Agent teams/runtime behavior: [docs/agent_teams.md](docs/agent_teams.md)
- Observability and tracing contracts: [docs/observability.md](docs/observability.md)
- MCP runtime/config and OAuth: [docs/mcp_integration.md](docs/mcp_integration.md)
- Database/document schema: [docs/db_schema.md](docs/db_schema.md)
- SCSS conventions and shared UI contracts: [docs/scss_style_guide.md](docs/scss_style_guide.md)

### Remote participant URL/transport reference

- Invitation page URL: `/chat/<session_id>/remote-user/<token>/`
- Remote WebSocket: `/ws/chat/<session_id>/remote-user/<token>/`
- Remote attachments endpoint: `/chat/sessions/<session_id>/remote/attachments/`
- Remote heartbeat endpoint: `/chat/sessions/<session_id>/remote/heartbeat/`

---

## Human-In-The-Loop (HITL) Gate

When Human Gate is enabled for a project, the run pauses after each round. The
bottom input bar switches to **gate mode**:

- A status badge appears in chat: `⏸ Round N/M — response is required`
  (single-assistant shows `Round N`, no max).
- The **Stop** button stays visible so the user can stop at any time.
  Clicking Stop disables the button immediately to prevent double-submission;
  it re-enables automatically when the next run starts.
- Typing in the send box and pressing **Send** (or Enter) resumes the run,
  forwarding the typed text as context for the next round.
- The Approve / Reject decision shortcuts have been removed; users type their
  response directly.

HITL notes sent with Send are rendered as markdown in both live chat and
persisted session history.

### Multi-User Collaboration (remote participants)

Multi-assistant projects with Human Gate enabled may also configure **remote
users** under *Project Configuration → Human Gate*. Each remote user has a
`name` and a `description` (used by Selector routing). A `quorum` selector
controls how many remote responses are required to continue past a Human
Gate pause:

- `yes` — wait for **all** required remote users **and leader response**.
- `first_win` — **any one responder** satisfies quorum (leader or any required remote).
- `team_config` — the **agent team** decides which remote users must reply for the active gate round; leader response is not required for this mode.

Runtime architecture note:

- When Human Gate is enabled and remote users are configured, team build adds
	one non-blocking `UserProxyAgent` participant per remote user (leader
	excluded) for roster/context awareness. These proxies intentionally return
	a sentinel "collected through Human Gate panel" message instead of blocking
	for inline input.
- Human input collection remains in the existing gate flow (WebSocket + Redis),
	not inline blocking prompts inside AutoGen turns.

- If no remote users are selected/online for the run, quorum handling falls
	back to the current leader-only Human Gate behavior for all quorum modes.
- Leader continue is enforced server-side: if required remote responses are
	still pending, the continue request is rejected with
	`409 {status:"awaiting_remote_users"}`.
- The readiness lobby now uses push updates through the leader websocket
	channel; the run auto-starts once every checked remote user is online.
- Leader remains the only control-plane actor for hard stop/resume endpoints;
  quorum satisfaction determines eligibility to continue, not endpoint authority.
- Once quorum is satisfied, queued remote replies are merged into the next
	run task as a "Remote participant responses" context block (and queued
	remote attachment IDs are merged into the resumed run payload).

The local user (the person running this app) is the **session leader**: they
own MCP authorizations and start every run. Remote users only join an active
chat session via a per-session **invitation URL** that the leader copies and
shares.

Before each run starts, the leader sees an in-history **readiness lobby**
listing every configured remote user with a checkbox, a Copy/Generate
Invitation Link button, and an Online/Offline status pill. The run starts
automatically once every checked user is online. Invitation URLs are
idempotent and stable for `REMOTE_USER_TOKEN_TTL_SECONDS` (default 12 h) —
repeated Copy clicks return the same URL during that window.

> Remote collaboration includes configuration, readiness lobby, and a
> dedicated remote-user chat page with turn-gated reply submission,
> attachment upload, copy-to-clipboard parity, and export modal access via
> delegated capability tokens.

Full reference (config, lifecycle, endpoints, Redis keys, security rules):
[docs/human_gate_remote_users.md](docs/human_gate_remote_users.md).

Single-assistant contract:

- With exactly one assistant, the project runs in **chat mode**.
- Team Setup is hidden in configuration for this mode.
- Human Gate is mandatory.
- The run pauses after each assistant turn and continues only when the human
  sends a reply; conversation ends when the human clicks **Stop**.
- `max_iterations` is not used as an auto-completion condition in
  single-assistant chat mode.
- **Send requires a message or attachment** in single-assistant mode.
  The button stays disabled until text is typed or a file is attached.
  An empty send is rejected at the backend with HTTP 400.

Multi-assistant gate contract:

- Gate pauses after each full agent round (all agents have spoken once).
- Send may be submitted with an empty textarea — agents resume with the
  accumulated history as context. An empty resume injects a synthetic
  `"Continue."` user turn into the model context so Anthropic Claude 3.7+ /
  Claude 4+ models (which require conversations to end with a user message)
  do not reject the API call. The synthetic turn is not shown in chat and
  not persisted to the discussion history.
- Stop triggers a graceful termination: the current agent finishes its turn,
  the message is persisted, then the run ends cleanly, showing the
  **Continue session** card in chat history.

**Stopped / completed session restart**: after a run ends, a restart card
appears with two options:
- **Continue from last** — resumes from the persisted agent state checkpoint.
- **Add context and continue** — lets you type extra context before resuming.

Typing in the main send box while the restart card is visible always starts a
**new** session, not a resume.

## Copying Chat Messages

Every chat bubble (user and agent) shows a **copy icon** in the message header.
Clicking it copies the message to your clipboard:

- Text is copied in **markdown format** (the raw source, not rendered HTML).
- Any attached files are appended as a markdown list below the text:
  ```
  **Attachments:**
  - report.pdf
  - screenshot.png
  ```
- The icon briefly changes to a check mark (✓) to confirm the copy succeeded.

The copy button is always visible regardless of whether a Secret Key is
entered — you can copy agent responses in both read and write sessions.

## Chat Attachments

Home chat composer and HITL gate textarea support:

- attach button file picker
- drag and drop
- paste from clipboard

### Supported file types

| Category | Extensions |
|---|---|
| **Images** | `png`, `jpg`, `jpeg`, `webp`, `gif`, `bmp`, `svg`, `heic`, `heif`, `tif`, `tiff` |
| **Documents** | `pdf`, `docx`, `doc` |
| **Spreadsheets** | `xlsx`, `xls`, `csv` |
| **Presentations** | `pptx`, `ppt` |
| **Text / Data** | `txt`, `md`, `json` |

Max file size: **20 MB** per file. Max files per message: **10**.

### Upload flow

1. **Select** — Use the attach button, drag-and-drop onto the composer, or paste from clipboard. The file is validated immediately (type, size, count).
2. **Upload** — The file is uploaded to Blob Storage (Azure in current implementation) in the background *before* the message is sent. A chip appears in the composer with a loading indicator during upload.
3. **Bind** — Once the upload succeeds the chip is bound with an `attachment_id`. Multiple files upload in parallel and each binds independently.
4. **Send** — When you press **Send** (or **Continue** in the HITL gate), the bound `attachment_id`s are submitted alongside your message text. The server validates session ownership for every ID.
5. **Agent context** — The server assembles the full agent task: extracted text from documents is appended as an `--- Attachments:` block; image bytes are injected as vision frames. The agent receives the complete content without truncation.

If an upload fails the chip is removed and an error is shown — no partial IDs are ever submitted.

### How attachments reach the agent

**Text files** (PDF, Word, Excel, CSV, PPTX, TXT, MD, JSON) — content is extracted
lazily the first time an agent run references the attachment:

1. The app checks Redis for a cached copy
   (`REDIS_ATTACHMENT_TTL_SECONDS`, default 24 h).
2. Cache **hit** → text returned immediately (< 1 ms).
3. Cache **miss + success** → bytes downloaded from Azure Blob, text extracted,
   stored in Redis, then returned. Full content is passed — no truncation.
   Genuinely empty documents (e.g. scanned PDFs with no text layer) are cached
   to avoid repeated blob downloads.
4. Cache **miss + exception** → blob download or extraction error: result is
   **not** stored in Redis. The next agent run retries the extraction, so
   transient failures do not become permanent.

For Excel files, every sheet is extracted with its name as a heading and
rows formatted as tab-separated values, preserving the tabular structure
for the model.

**Images** — downloaded from blob on each run and passed as raw pixel data
via `MultiModalMessage` to vision-capable models. Set `"vision": true` in
`agent_models.json` for the model being used (see [Models & `model_info`](#models--model_info)).

### Storage

All blobs are stored under `sessions/{session_id}/attachments/{attachment_id}/{filename}`
in **Azure Blob** (current implementation — swappable via `ATTACHMENT_STORAGE_PROVIDER`).
MongoDB stores only metadata — no file content in the database.
Blobs, Redis cache, and metadata rows are all cleaned up when a session is deleted.

Storage abstraction pattern:

- **Strategy**: provider-specific byte operations (`StorageStrategy`)
- **Factory**: runtime provider selection (`build_storage_strategy()`)
- **Repository-style metadata access**: attachment metadata queries scoped by `session_id`

---

## Active Session Coordination (Redis)

Active chat runs are coordinated through Redis by `session_id` while the run is
in progress:

- Exactly one worker can hold the active run lease for a session.
- A heartbeat renews the lease while streaming.
- `/chat/sessions/<id>/stop/` sets a Redis cancel signal so cancellation works
	across containers/pods.
- If Redis is unavailable, run start fails fast and does not transition the
	session into `running`.

MongoDB remains the durable source of truth for discussion history and
persisted AutoGen team resume state (`chat_sessions.agent_state`).

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

## Models & `model_info`

The model catalog lives in [`agent_models.json`](agent_models.json). Each key
is the model name shown in the UI; the value declares the provider, optional
endpoint/version overrides, and — crucially — a `model_info` capability map.

```jsonc
{
  "gpt-5.4-mini": {
    "provider": "azure_openai",
    "api_version": "2024-12-01-preview",
    "model": "gpt-5.4-mini-2026-03-17",
    "model_info": {
      "function_calling": true,
      "json_output": true,
      "structured_output": true,
      "vision": true,
      "family": "gpt-5"
    }
  }
}
```

`model_info` advertises model capabilities to AutoGen. The factory default
is conservative — every flag is `false` — so any provider that always
forwards `model_info` (Azure OpenAI, Azure Anthropic, Google Gemini) **must**
declare overrides per model in `agent_models.json`, otherwise the model will
behave as if it has no capabilities at all.

| Field | Set `true` when… |
| --- | --- |
| `function_calling` | The model supports tool / function calling |
| `json_output` | The model can return JSON-mode responses |
| `structured_output` | The model supports a JSON schema for structured output |
| `vision` | The model accepts image inputs |
| `family` | Identifier such as `gpt-5`, `gpt-4.1`, `claude-sonnet-4`, `deepseek-v3` |

### `function_calling` is required for MCP tools

Whenever an agent has `mcp_tools` set to `shared` or `dedicated`, AutoGen
forwards the attached `McpWorkbench` tools to the model client. If the
resolved `model_info.function_calling` is `false`, the underlying client
raises:

```text
ValueError: Model does not support function calling
```

So when introducing a new model that should be usable with MCP tools, the
model's catalog entry **must** declare `"function_calling": true`. Models
that do not support tool calling (some reasoning, audio, embedding) must be
paired only with agents whose `mcp_tools = "none"`.

For the `openai` and `anthropic` direct providers, AutoGen has an internal
table of known model names (e.g. `gpt-4o`, `claude-3-7-sonnet`), so
`model_info` can be omitted for those; for any custom or unrecognized name,
declare it explicitly.

See [`docs/agent_factory.md`](docs/agent_factory.md) for the full provider
registry, environment variable conventions, and per-provider field
references.

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

**Secrets** — `mcp_secrets` is the single global secret store for MCP in a
project. It is shared by both:

- project-level `shared_mcp_tools`, and
- per-assistant `mcp_configuration` when `mcp_tools = dedicated`.

Never embed raw API keys or tokens directly in either MCP JSON field. Store
them in `mcp_secrets` (`{KEY: value}`, `KEY` must be `UPPER_SNAKE`) and
reference them via `{KEY_NAME}` placeholders.

Example (one global `mcp_secrets` consumed by both Shared and Dedicated MCP):

```jsonc
{
	"mcp_secrets": {
		"GITHUB_PAT": "ghp_xxx",
		"NOTION_TOKEN": "secret_xxx"
	},
	"shared_mcp_tools": {
		"mcpServers": {
			"github": {
				"transport": "http",
				"url": "http://mcp-gateway:9000/github/mcp",
				"headers": {
					"Authorization": "Bearer {GITHUB_PAT}"
				}
			}
		}
	},
	"agents": [
		{
			"name": "research_assistant",
			"mcp_tools": "dedicated",
			"mcp_configuration": {
				"mcpServers": {
					"notion": {
						"command": "npx",
						"args": ["-y", "@notionhq/notion-mcp-server"],
						"env": {
							"NOTION_TOKEN": "{NOTION_TOKEN}"
						}
					}
				}
			}
		}
	]
}
```

Values are masked in the edit form, hidden in the readonly view, and
substituted at runtime only inside `agents/mcp_tools.py`. The OTel
`fingerprint` attribute is computed over the placeholder form so it stays
stable across secret rotations.

### OAuth 2.0 for HTTP MCP servers

Some HTTP MCP servers issue Bearer tokens via OAuth 2.0 instead of static API keys.
Configure this per-server under **MCP OAuth App Registrations** in the project config form.

```jsonc
{
  "mcp_oauth_configs": {
    "my-api-server": {
      "auth_url":      "https://provider.example.com/oauth/authorize",
      "token_url":     "https://provider.example.com/oauth/token",
      "client_id":     "app-client-id",
      "client_secret": "app-client-secret",
      "scopes":        "read write"
    }
  }
}
```

- **`server_name`** must match a key inside your `mcpServers` JSON (shared or dedicated).
- Register the callback URL `{BASE_URL}/mcp/oauth/callback/` with your OAuth provider.
- Before each agent run, the server checks Redis for session-scoped Bearer tokens (`POST /run/` returns **HTTP 409** when any are missing) and the UI shows an **Authorize** panel in the chat history. Clicking the Authorize button opens a popup; after the user grants access the run resumes automatically.
- **Test Authorization** (config form) validates credentials without starting a run.
- Tokens are session-scoped and expire from their JWT `exp` claim (TTL = `exp − now(UTC)`). Falls back to a hardcoded 3 h default if `exp` is absent. There is no mid-session refresh (v1) — re-authorize on the next run if a token expires.
- `client_secret` is stored masked and never sent to the browser after the first save.
- The OAuth start endpoint (`/mcp/oauth/start/`) is opened from a popup window, which cannot set request headers — it accepts the admin secret as `X-App-Secret-Key` **or** `?skey=<APP_SECRET_KEY>`. Always serve the app over TLS and scrub query strings from access logs (or accept the leak as in-scope for an admin-only deployment).
- Every branch of the OAuth start + callback handlers emits structured `agents.mcp.oauth_*` log events plus three nested OpenTelemetry spans (`mcp.oauth.start`, `mcp.oauth.callback`, `mcp.oauth.token_exchange`) so popup-window failures are diagnosable from the server console alone.

Full documentation: [docs/mcp_integration.md](docs/mcp_integration.md).

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `APP_SECRET_KEY` | Admin password for write access | *(required)* |
| `MONGODB_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGODB_NAME` | MongoDB database name | `product_discovery` |
| `REDIS_URI` | Redis connection string used for active run coordination | `redis://localhost:6379/0` |
| `REDIS_NAMESPACE` | Redis key namespace prefix | `product_discovery` |
| `REDIS_RUN_LEASE_TTL_SECONDS` | Active run lease TTL (seconds) | `300` |
| `REDIS_RUN_HEARTBEAT_SECONDS` | Lease heartbeat interval (seconds) | `20` |
| `REDIS_CANCEL_SIGNAL_TTL_SECONDS` | Cancel signal TTL (seconds) | `120` |
| `REMOTE_USER_TOKEN_TTL_SECONDS` | Lifetime of a remote-user join-URL token (seconds). Redis is short-lived run state — the leader's next Copy click after expiry mints a fresh token. | `43200` (12 h) |
| `REMOTE_USER_PRESENCE_TTL_SECONDS` | How long a remote user is considered online without a heartbeat refresh | `60` |
| `REMOTE_USER_HEARTBEAT_INTERVAL_SECONDS` | Presence heartbeat cadence used by remote-user pages | `30` |
| `REMOTE_USER_CHECKED_TTL_SECONDS` | Lifetime of the leader's checked-set in Redis. This remains semantically separate from token TTL even if both values are configured equal. | `43200` (12 h) |
| `REDIS_ATTACHMENT_TTL_SECONDS` | How long extracted attachment text is kept in Redis (seconds). Raise this if sessions span multiple days. | `86400` (24 h) |
| `MAX_AGENT_STATE_BYTES` | Maximum byte size of serialised AutoGen agent state stored in MongoDB. Raise for long sessions with many attachments or embedded images. MongoDB's document limit is 16 MB (shared with `discussions[]`). | `1000000` (1 MB) |
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
| `ATTACHMENT_STORAGE_PROVIDER` | Attachment storage backend selector | `azure` |
| `AZURE_STORAGE_CONTAINER_SAS_URL` | Azure Blob container SAS URL (includes token in query string) used for chat attachments | *(required when attachments are enabled)* |

Attachment SAS guidance:

- Use a container-scoped SAS URL (not account-level keys).
- Include permissions required by the attachment pipeline: create/write/read/list/delete.
- Prefer short TTL and rotation for operational safety.

Redis URI examples:

- ACL username + password:
	- `REDIS_URI=redis://REDIS_USER:REDIS_PASS@REDIS_HOST:REDIS_PORT/REDIS_DB`
	- Example with db `0`: `REDIS_URI=redis://user:password@REDIS_HOST:REDIS_PORT/0`
- Password-only auth (older/default setup, no username):
	- `REDIS_URI=redis://:password@REDIS_HOST:REDIS_PORT/0`

---

## Observability

OpenTelemetry is the project-wide observability standard. Every layer (Django
requests, outbound HTTP to Trello/Jira, MongoDB, service mutations, AutoGen
agent runs) emits spans linked into a single trace per request via the
propagated `X-Request-ID` header. Logs and traces are intentionally split:

- **Logs** — JSON to stderr, every line carries `request_id`, `trace_id`, `span_id`. Lifecycle events + `WARNING`+ only; per-call HTTP success detail lives on spans.
- **Traces** — full request/response payloads (redacted, truncated at 32 KB) shipped to the configured OTLP backend (Langfuse today; pluggable). `OTEL_CONSOLE_EXPORTER=error` additionally dumps any failed span to stderr with its full attribute set + stacktrace.

### Trace model for agent runs

Each `POST /chat/sessions/<id>/run/` (a single agent round) produces its own
**root trace** (`agents.session.run`). This trace is intentionally severed
from the Django HTTP request span so every round is independently queryable in
Langfuse with a distinct `trace_id`. The root span's W3C traceparent string is
stored in Redis (alongside the run lease) and reattached inside the SSE
generator body, so all `agents.*` log lines and child spans (`autogen.event.*`,
`mcp.tool.*`, `@traced_function` service calls) share the same `trace_id`.

A human-gate resume starts a fresh `/run/` call and therefore a new
`agents.session.run` trace — each round of a multi-round conversation appears
as a separate trace. You can filter Langfuse by `session_id` attribute to see
all rounds for a session.

```
agents.session.run                              [root — fresh trace_id per round]
├─ @traced_function on service entry            [always on]
│   └─ service.* mutation
└─ AutoGen agent run                            [bridge · OTEL_INSTRUMENT_AGENTS]
   ├─ autogen.event.<type>    (LLM call)
   │  └─ HTTP POST <provider> (LLM API)         [auto · OTEL_INSTRUMENT_HTTP]
   ├─ mcp.tool.request <name>                   [ToolCallRequestEvent · DEBUG]
   └─ mcp.tool.result <name>                    [ToolCallExecutionEvent · DEBUG]
```

When Redis or OTel is unavailable the run continues normally; log lines show
`trace_id: "-"` (same as the pre-feature default) and no error is raised.

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
