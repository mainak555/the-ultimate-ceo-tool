[![Watch the video](https://img.youtube.com/vi/yC6rH_hbtqs/maxresdefault.jpg)](https://youtu.be/yC6rH_hbtqs)

# CouncilAI

### The Ultimate CEO Tool — Turn Vision into Action

> *Agentic AI for teams, not individuals — where specialized human experts and AI agents govern together.*

📖 **[Project Charter](PROJECT_CHARTER.md)** — Vision, market thesis, team collaboration model & flowchart

---

CouncilAI turns strategy into execution by helping leadership teams move from OKRs and vision docs to actionable product backlogs with accountable human+AI collaboration loops.

**What is novel / why it matters in-market:** team-native agentic governance (not single-user AI chat), quorum-based human gates, and direct Trello/Jira export so outputs land in the tools organizations already trust.

**Tech stack:** Django ASGI SPA, HTMX, SCSS, MongoDB (PyMongo), Redis coordination, AutoGen multi-agent runtime, WebSocket/SSE streaming, Azure Blob attachments.

**Production-grade observability:** structured JSON logs with request/trace correlation, OpenTelemetry spans across HTTP + services + agents (+ optional Mongo), payload redaction/truncation, and OTLP backend compatibility (Langfuse-ready).

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

## Human-In-The-Loop (HITL) Gate

When Human Gate is enabled for a project, the run pauses after each round. The
bottom input bar switches to **gate mode**:

- A status badge appears in chat: `⏸ Round N/M — response is required`.
  Pure single-assistant mode (no remote users) shows `Round N` with no max.
- The **Stop** button stays visible so the user can stop at any time.
  Clicking Stop disables the button immediately to prevent double-submission;
  it re-enables automatically when the next run starts.
- Typing in the send box and pressing **Send** (or Enter) resumes the run,
  forwarding the typed text as context for the next round.
- Notes sent with Send are rendered as markdown in both live chat and
  persisted session history.

## Guest Readonly View

Host page provides a **Guest share** action above the message box. It generates
and copies a per-session guest URL.

Guest page behavior:

- Readonly only: header + chat history panel
- No send box, no attach controls, no mutation actions
- Live updates stream through WebSocket while the host run is active

Guest links are standalone and do not require remote-user configuration.

Attachment rendering and copy behavior are standardized across Home, Remote, and
Guest surfaces so users see the same thumbnail/icon fallback and message-copy
payload format regardless of page.

---

### Mode reference

There are three distinct runtime modes depending on the number of assistant
agents and whether any remote users are configured.

| Configuration | Team Setup visible? | Iteration limit? | Empty Continue allowed? |
|---|---|---|---|
| 1 assistant, no remote users | No (hidden) | No — runs until **Stop** | No — text or attachment required |
| 1 assistant, ≥ 1 remote user | **Yes** (honored) | Yes — `max_iterations` | Yes |
| ≥ 2 assistants | **Yes** (honored) | Yes — `max_iterations` | Yes |

**Selector team** type requires ≥ 2 assistant agents OR (≥ 1 assistant agent & ≥ 1 remote user)

---

### Pure single-assistant chat mode (1 assistant, no remote users)

- Team Setup is hidden in project configuration.
- Human Gate is mandatory and cannot be disabled.
- The run pauses after each assistant turn and continues only when the human
  sends a reply; the conversation ends when the human clicks **Stop**.
- `max_iterations` is **not** used — there is no automatic completion.
- **Send requires a message or attachment.** The button stays disabled until
  text is typed or a file is attached. An empty send is rejected with HTTP 400.

---

### Single-assistant + remote users (1 assistant, ≥ 1 remote user)

- **Team Setup is visible** and its values are honored.
- Human Gate is still mandatory and cannot be disabled.
- The run pauses after each assistant turn, exactly as above, but now
  completes automatically when `current_round` reaches `max_iterations`.
- Empty Continue is allowed (same as multi-assistant).
- **Quorum**: configure how many remote users must respond before the run
  resumes — *Wait for all*, *First response wins*, or *Agent planner decides*.
  Quorum is only applied when at least one remote user is listed; it has no
  effect in the pure single-assistant or multi-assistant-only cases.
- `first_win` resumes automatically when the first accepted responder (host or
  remote) commits the round.
- Late responders may receive a non-fatal race response (`stale` / `locked`)
  while the run is already resuming.
- If a required remote user disconnects while a run is already in progress,
	the current run continues. The next run start is blocked by the remote
	participants readiness card until required users are online again (or
	explicitly ignored by the host).

#### Adding remote users

In the project **Human Gate** section:
1. Click **+ Add Remote User**.
2. Enter a **Name** (must be a valid identifier, e.g. `product_owner`).
3. Optionally add a **Description** to give the agent team context about this
   participant's role.
4. Repeat for each additional remote user.
5. Select the **Quorum** that controls when the run resumes.
6. Save the configuration.

#### Allowing a remote user to export

When Trello or Jira integrations are enabled on a project, the host can grant
individual remote users the ability to open the export modal directly from
agent message bubbles on their page.

In the remote participants readiness card on the home page:

1. Each remote user row shows a **Can Export** checkbox.
2. Check the box to grant access — a short-lived, per-user export key is generated
   in Redis and delivered to the remote user's page in real time via WebSocket.
   Export action buttons appear immediately on all agent message bubbles without
   a page reload.
3. Uncheck the box (or click **Ignore**) to revoke access — export buttons
   disappear on the remote page instantly.
4. The export key is used for all Trello and Jira session-scoped API calls. The
   admin `APP_SECRET_KEY` is **never** exposed to the remote user.
5. Export keys share the same TTL as invite tokens (`REDIS_REMOTE_USER_TOKEN_TTL_SECONDS`,
   default 6 h) and are automatically purged when the session is deleted.

---

### Multi-assistant gate contract (≥ 2 assistants)

- Gate pauses after each full agent round (all agents have spoken once).
- Send may be submitted with an empty textarea — agents resume with the
  accumulated history as context.
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

## Project Versioning

Every project carries a server-managed `version` float displayed as **v1.0**, **v1.1**, etc.
alongside the project name in the sidebar, the edit form header, and the readonly view.

| Action | Version rule | Example |
|---|---|---|
| Create new project | Starts at `1.0` | → `v1.0` |
| Save / update | Bumps `+0.1` only when `team.type` changes or `human_gate.quorum` departs `team_choice`; unchanged otherwise | `v1.0 → v1.1` |
| Clone | Bumps to next whole number | `v1.x → v2.0`, `v2.x → v3.0` |

The version is **set by the server only** — it is never exposed as an editable form field
and is never accepted from user input. Legacy projects (created before this feature)
display as `v1.0` without any migration step required.

When you clone a project, the clone starts at the next whole-number version so you can
immediately tell which generation of a configuration a chat session was created against
(each session snapshot the project version at creation time).

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

## Documentation Map (Contributors)

Use this map to avoid duplicated guidance:

- Policy rules and repository contracts: [AGENTS.md](AGENTS.md)
- **Chat session lifecycle** (run guards, SSE events, quorum, attachments, Redis keys): [docs/chat_session_lifecycle.md](docs/chat_session_lifecycle.md)
- Architecture-level UI contracts: [docs/UI.md](docs/UI.md)
- Frontend module ownership and reuse boundaries: [docs/frontend_js_architecture.md](docs/frontend_js_architecture.md)
- Shared chat layout/surface implementation checklist: [`.agents/skills/chat_surface_shared/SKILL.md`](.agents/skills/chat_surface_shared/SKILL.md)
- Shared compose/send/attachment checklist: [`.agents/skills/chat_compose_attachment_contract/SKILL.md`](.agents/skills/chat_compose_attachment_contract/SKILL.md)

Keep implementation detail in skills and keep AGENTS/docs concise.

### Frontend Standardization Contract (Contributors)

- Reuse-first: if a chat helper is needed in 2+ surfaces, place it in
	`server/static/server/js/chat_surface_utils.js` instead of duplicating it in
	`home.js`, `remote_user.js`, or `guest_user.js`.
- Keep surface-specific run/gate/quorum logic in feature files.
- DOM id naming convention:
	- Home ids use `chat-*`
	- Remote ids use `remote-chat-*`
	- Guest ids use `guest-chat-*`
- Keep canonical container/history ids stable:
	- `chat-messages` / `chat-history-msgs`
	- `remote-chat-messages` / `remote-chat-history-msgs`
	- `guest-chat-messages` / `guest-chat-history-msgs`

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
- Before each agent run, the server checks Redis for session-scoped Bearer tokens. When any are missing, `POST /run/` returns **HTTP 409** and the UI renders an **Authorize** panel in the chat history. Clicking **Authorize** opens a consent popup; readiness updates arrive via a WebSocket connection (`ws/mcp/oauth/<session_id>/`) driven by Redis pub/sub — there is no polling.
- Once all servers are authorized the WebSocket sends a `complete` signal and the run resumes automatically.
- If a mid-run token expires, the SSE stream emits an `awaiting_mcp_oauth` event and the same Authorize panel reappears.
- **Test Authorization** (config form) validates credentials without starting a run.
- Tokens are session-scoped and expire from their JWT `exp` claim (TTL = `exp − now(UTC)`). Falls back to a hardcoded 3 h default if `exp` is absent. There is no mid-session refresh (v1) — re-authorize on the next run if a token expires during a run.
- `client_secret` is stored masked and never sent to the browser after the first save.
- The OAuth start endpoint (`/mcp/oauth/start/`) is opened from a popup window, which cannot set request headers — it accepts the admin secret as `X-App-Secret-Key` **or** `?skey=<APP_SECRET_KEY>`. Always serve the app over TLS and scrub query strings from access logs (or accept the leak as in-scope for an admin-only deployment).
- Every branch of the OAuth start, callback, and WebSocket consumer emits structured `agents.mcp.oauth_*` log events plus three nested OpenTelemetry spans (`mcp.oauth.start`, `mcp.oauth.callback`, `mcp.oauth.token_exchange`) so popup-window failures are diagnosable from the server console alone.

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
| `REDIS_SOCKET_TIMEOUT` | Redis socket operation timeout (seconds) for the shared sync Redis client used by session coordination and Redis-backed helpers. This bounds read/write waits after a connection is established; lower values fail faster under Redis stalls, higher values tolerate transient latency. Note: async WebSocket pub/sub clients do not inherit this unless passed explicitly at `aioredis.from_url(...)` call sites. | `2.0` |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | Redis connection-establishment timeout (seconds) for the shared sync Redis client. This controls how long the app waits to open a TCP connection to Redis before treating Redis as unavailable; keep this low for fast failover behavior. Note: async WebSocket pub/sub clients do not inherit this unless passed explicitly at `aioredis.from_url(...)` call sites. | `2.0` |
| `REDIS_RUN_LEASE_TTL_SECONDS` | Active run lease TTL (seconds) | `300` |
| `REDIS_RUN_HEARTBEAT_SECONDS` | Interval (seconds) between lease-renewal heartbeats for an active run owner. The runner periodically renews the session lease; if renew fails, the run is cancelled to prevent split-brain execution across workers. Keep this comfortably below `REDIS_RUN_LEASE_TTL_SECONDS`. | `20` |
| `REDIS_CANCEL_SIGNAL_TTL_SECONDS` | TTL (seconds) for the cross-worker cancel key set by stop actions. Running loops check this key and cancel promptly; TTL auto-cleans stale cancel keys if a worker crashes before cleanup. | `120` |
| `REDIS_ATTACHMENT_TTL_SECONDS` | TTL (seconds) for extracted non-image attachment text cached in Redis. On cache miss, text is re-extracted from blob storage; on hit, runs resume quickly without reprocessing. Increase for long-lived sessions, decrease to reduce Redis memory footprint. | `86400` (24 h) |
| `REDIS_GATE_RESPONSE_TTL_SECONDS` | TTL (seconds) for per-responder human-gate quorum keys (responses and winner-claim key). Supports delayed human responses while ensuring old gate rounds expire automatically and do not leak across later rounds. | `21600` (6 h) |
| `REDIS_PENDING_TASK_TTL_SECONDS` | TTL (seconds) for quorum-composed pending task payloads stored between respond and next run. Short-lived by design: protects against stale composed tasks while allowing immediate resume calls to consume data via atomic pop. | `300` |
| `REDIS_REMOTE_USER_TOKEN_TTL_SECONDS` | TTL (seconds) for remote-user and guest invitation token mappings in Redis. Also reused by related short-lived remote-session markers (for example ignored status, session quorum override, and guest online marker), so changing this value affects invitation validity and some readiness-state durability. | `21600` (6 h) |
| `REDIS_REMOTE_USER_ONLINE_STATUS_TTL_SECONDS` | TTL (seconds) for remote-user online presence keys only. Presence is refreshed while the remote chat socket is alive and naturally expires if the client disconnects unexpectedly; ignored status is handled separately and is not governed by this TTL. | `300` (5 min) |
| `REDIS_REMOTE_USER_TTL_REFRESH_INTERVAL_SECONDS` | Refresh cadence (seconds) for extending remote-user online presence TTL from the RemoteChat WebSocket loop. Keep this lower than `REDIS_REMOTE_USER_ONLINE_STATUS_TTL_SECONDS` to avoid accidental offline transitions during normal connected sessions. | `60` |
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
| `ANTHROPIC_MAX_RETRIES` | Maximum retry attempts when Anthropic returns HTTP 529 (OverloadedError). Each retry uses exponential backoff starting at `ANTHROPIC_RETRY_BASE_DELAY` seconds. Applies to both `anthropic` and `azure_anthropic` providers. | `2` |
| `ANTHROPIC_RETRY_BASE_DELAY` | Base delay in seconds for the first Anthropic 529 retry. Subsequent retries double the delay (plus up to 1 s of random jitter). Set to `0` to disable delay (not recommended in production). | `5.0` |
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

## Project Versioning

Every project document carries a server-managed `version` float displayed as `vX.Y` in the sidebar and config page header.

| Event | Version rule |
|-------|-------------|
| **Create** | Starts at `1.0` |
| **Save / Edit** | Bumps `+0.1` only when `team.type` changes OR `human_gate.quorum` departs `team_choice`; unchanged for all other field edits. Multiple conditions in one save still produce exactly one `+0.1`. Bump logic lives in `_compute_version_bump(existing, cleaned)` in `server/services.py` — extend there. |
| **Clone** | Integer part bumped by 1, decimal reset: `1.x → 2.0`, `2.x → 3.0` |

The field is computed entirely server-side in `server/services.py`. It is never exposed as a user-editable input. Legacy documents (written before this field was added) default to `1.0` on read via `normalize_project()`. To persist the default to MongoDB, run:

```js
db.project_settings.updateMany({ version: { $exists: false } }, { $set: { version: 1.0 } });
```

---

## Export Schema Contract (Trello)

> **Note:** This section is the admin-facing quick reference (required by AGENTS rule 47). The developer-facing canonical is [docs/trello_integration.md](docs/trello_integration.md) — update both in the same change when the schema changes.

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

> **Note:** This section is the admin-facing quick reference (required by AGENTS rule 47). The developer-facing canonical is [docs/jira_integration.md](docs/jira_integration.md) — update both in the same change when the schema changes.

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
