# Architecture

## Project Structure

```
product-discovery/
├── .agents/                     # Copilot skills and implementation playbooks
│   └── skills/
├── agents/                      # Root AutoGen runtime package
│   ├── integrations/            # Jira/Trello export clients + LLM extractor
│   ├── mcp_tools.py             # MCP workbench/session wiring
│   ├── session_coordination.py  # Redis run ownership + remote readiness/export keys
│   └── team_builder.py          # Team construction + termination wiring
├── core/                        # Shared cross-cutting infrastructure modules
│   ├── tracing.py               # OpenTelemetry wiring/helpers used by server + agents
│   └── http_tracing.py          # Shared outbound HTTP span enrichment helpers
├── config/                      # Django project package (settings, root URLs, ASGI/WSGI)
├── deployments/                 # Deployment topologies and Docker artifacts
│   ├── standalone/
│   │   └── Dockerfile           # Standalone app image build
│   ├── compose/
│   │   ├── docker-compose.yml
│   │   └── Dockerfile.mcp-gateway
│   └── k8s/
├── docs/                        # Project documentation
├── examples/                    # Example prompts and starter configurations
├── server/                      # Main Django app
│   ├── db.py                    # MongoDB connection singleton (PyMongo)
│   ├── schemas.py               # Input validation
│   ├── services.py              # Business logic (CRUD, auth verification)
│   ├── views.py                 # Home/HTMX controllers
│   ├── remote_user_views.py     # Remote-user page/controllers
│   ├── guest_views.py           # Guest page/controllers
│   ├── mcp_views.py             # MCP OAuth/config endpoints
│   ├── consumers.py             # WebSocket consumers
│   ├── routing.py               # Channels routing
│   ├── attachment_service.py    # Upload/extraction/cache/delete pipeline
│   ├── storage_backends.py      # Pluggable blob storage (Strategy pattern)
│   ├── templates/server/
│   │   ├── home.html            # Home/HITL chat shell
│   │   ├── remote_user.html     # Remote user public shell
│   │   ├── guest_user.html      # Guest readonly shell
│   │   └── partials/
│   └── static/server/
│       ├── js/                  # Frontend feature/shared modules
│       └── scss/                # SCSS source (compiled by django-compressor)
├── staticfiles/                 # Compressed asset cache output
├── agent_models.json            # Shared model catalog keyed by model name
├── AGENTS.md                    # Repository policy and rules
├── chat_session.md              # Chat session/reference notes
├── README.md                    # User + contributor guide
├── requirements.txt             # Python dependencies
└── manage.py                    # Django management entrypoint
```

## Layer Responsibilities

### `db.py` — Data Access
- Provides `get_client()`, `get_db()`, `get_collection(name)`.
- Manages MongoDB connection as a module-level singleton.
- Defines canonical collection constants used across server modules: `PROJECT_SETTINGS_COLLECTION`, `CHAT_SESSIONS_COLLECTION`, `ATTACHMENTS_COLLECTION`.
- Creates collections/indexes on startup via `ensure_indexes()`:
	- `project_settings`: unique index on `project_name`
	- `chat_sessions`: index on `project_id`; unique partial index `(_id, discussions.id)`
	- `chat_attachments`: index on `session_id`; unique index `(session_id, attachment_id)`

### `schemas.py` — Validation
- `validate_project(data)` — validates and cleans project configuration data.
- `validate_agent(data)` — validates a single assistant agent entry.
- `validate_human_gate(data)` — validates the optional human approval gate.
- `validate_team(data, human_gate_enabled, assistant_count=None)` — validates team type and max iterations, including single-assistant chat-mode constraints.
- Returns cleaned `dict` or raises `ValueError` with a descriptive message.
- No database or request coupling.

### `services.py` — Business Logic
- `list_projects()` — returns all projects sorted by name.
- `get_project(project_id)` — returns a single normalized project by MongoDB ObjectId hex string or `None`.
- `create_project(data, initial_version=1.0)` — validates, inserts, sets `version` to `initial_version` (default `1.0`). Handles duplicate name errors.
- `update_project(project_id, data)` — validates, replaces existing document, conditionally bumps `version` by `0.1` via `_compute_version_bump(existing, cleaned)`. Bump triggers: (a) `team.type` changes, or (b) `human_gate.quorum` departs `team_choice`. All other field changes leave `version` unchanged.
- `clone_project(project_id)` — copies project as `"{name} - Copy"`, bumps major version (e.g. `1.x → 2.0`, `2.x → 3.0`).
- `delete_project(project_id)` — deletes only when no dependent chat sessions exist.
- `normalize_project(data)` — adapts old documents to the new nested shape for display.
- `get_available_models()` — returns the sorted model catalog used by the UI.
- `verify_secret_key(key)` — constant-time comparison against `APP_SECRET_KEY`.
- `verify_session_export_key(key, session_id)` — validates a per-user impersonated export key against Redis; used by session-scoped export endpoints.
- `has_valid_session_auth(request, session_id)` — accepts either `APP_SECRET_KEY` or a valid session export key; used by all session-scoped Trello and Jira endpoints so remote users with export access can call them.
- All functions work with plain dicts — no HTTP/request coupling.

Deletion policy:
- Never cascade delete chat sessions from project deletion.
- If chat sessions exist for a project, project deletion is blocked with a clear error.

### `views.py` — HTTP/HTMX Controllers
- Parses request data, calls service functions, renders HTMX partials.
- Checks `X-App-Secret-Key` request headers for write access.
- Returns `HX-Trigger` headers for cross-partial updates (e.g., sidebar refresh).
- `_build_agent_task_for_run(task_text, session_id, attachment_ids)` — returns `str | MultiModalMessage`. Downloads image bytes from blob, wraps them as `autogen_core.Image` objects inside `MultiModalMessage`. Falls back to plain string if vision imports or downloads fail.

### `attachment_service.py` — Chat Attachment Pipeline
Orchestrates the full lifecycle: upload validation → blob write → MongoDB metadata → lazy Redis-cache extraction → agent context assembly → session cleanup.
- **Upload** (`upload_session_attachments`): validates type/size/count, writes bytes to blob via `storage_backends.py`, persists metadata-only document to `chat_attachments` in MongoDB. No text extraction at upload time.
- **Extraction** (`build_attachment_context_block`): checks Redis for cached text first (`{REDIS_NAMESPACE}:attachment:{session_id}:{attachment_id}:text`). Cache miss → downloads from blob, extracts text by type, writes to Redis with `REDIS_ATTACHMENT_TTL_SECONDS` TTL, returns full text (no truncation). Supported types: PDF (50 pages), DOCX, PPTX (50 slides), XLSX/XLS (all sheets, tab-separated), CSV (200 rows), TXT, MD, JSON.
- **Vision** (`load_images_for_agents`): downloads raw image bytes from blob on every run/resume (images are never Redis-cached) and returns `list[tuple[filename, bytes, mime_type]]`.
- **Cleanup** (`delete_session_attachments`): purges Redis text-cache keys (`purge_session_attachment_cache`) → deletes blob prefix → deletes MongoDB metadata rows, in that order.
- Redis client is shared from `agents.session_coordination.get_redis_client()` (imported lazily inside `_get_redis()` to avoid circular imports).
- **Storage layer design**: see [docs/attachment_storage.md](attachment_storage.md) for the three-layer rationale (blob / metadata / Redis cache), data models, sequence diagrams for upload / agent-run / session-delete, and a decision guide for common tasks.

### `storage_backends.py` — Blob Storage Strategy
Implements a Strategy + Factory pattern for pluggable blob providers.
- `StorageStrategy` — abstract interface: `upload_bytes`, `download_bytes`, `delete_prefix`.
- `AzureBlobStorageStrategy` — current implementation; auth via `AZURE_STORAGE_CONTAINER_SAS_URL` (container SAS URL with query token).
- `build_storage_strategy()` — factory that reads `ATTACHMENT_STORAGE_PROVIDER` env var and returns the appropriate strategy instance.
- To add a new provider (e.g. S3): implement `StorageStrategy`, register in `build_storage_strategy()`.

### Root `agents/` Package — Runtime Integration
- `agents/config_loader.py` reads the shared `agent_models.json` catalog.
- `agents/factory.py` resolves provider-specific AutoGen model clients from model names.
- `agents/prompt_builder.py` resolves system prompts and appends the project objective.
- `agents/team_builder.py` builds AutoGen teams (`RoundRobinGroupChat` or `SelectorGroupChat`) from saved configuration. The team type is read from `project["team"]["type"]`. Each `AssistantAgent` receives `description=` (line 1 of its resolved system message) so that `SelectorGroupChat`'s `{roles}` placeholder renders meaningful routing context.
- Single-assistant projects run in chat mode with Human Gate enabled and a `RoundRobinGroupChat` runtime; selector routing requires at least two assistants.
- `agents/runtime.py` owns process-local team/cache lifecycle and MCP workbench teardown.
- `agents/session_coordination.py` owns Redis-backed active-session coordination (run lease, heartbeat, cross-instance cancel signaling), remote-user readiness ephemeral state (online/ignored status, per-session quorum override, deferred readiness latch), and per-user impersonated export keys (`generate_remote_user_export_key`, `revoke_remote_user_export_key`, `get_remote_export_key_data`, `get_all_remote_user_export_states`).

### Root `core/` Package — Shared Infrastructure
- `core/tracing.py` owns OpenTelemetry setup and helpers (`init_tracing`,
	`traced_function`, `traced_block`, `set_payload_attribute`).
- Shared by both Django app modules in `server/` and agent runtime modules
	in `agents/`.

Provider client resolution in `agents/factory.py` (builder-per-provider pattern):
- `openai`          → `OpenAIChatCompletionClient` — direct OpenAI API
- `anthropic`       → `AnthropicChatCompletionClient` wrapped in `_RetryAnthropicClient` — direct Anthropic API with HTTP 529 exponential-backoff retry
- `google`          → `OpenAIChatCompletionClient` — Google Gemini (OpenAI-compatible)
- `azure_openai`    → `AzureOpenAIChatCompletionClient` — Azure AI Foundry OpenAI deployment
- `azure_anthropic` → `AnthropicChatCompletionClient` wrapped in `_RetryAnthropicClient` with `base_url` — Anthropic model on Azure AI Foundry

`_RetryAnthropicClient` is a transparent proxy that retries `create()` and `create_stream()` on HTTP 529 (`OverloadedError`) using exponential backoff with jitter. All other attribute accesses delegate to the inner client unchanged. Retry count and base delay are configurable via `ANTHROPIC_MAX_RETRIES` (default `2`) and `ANTHROPIC_RETRY_BASE_DELAY` (default `5.0` seconds).

To add a new provider, define a `_build_<name>` function in `agents/factory.py` and add one entry to `_PROVIDER_BUILDERS`.

See [docs/agent_factory.md](agent_factory.md) for the full `agent_models.json` schema, environment variable reference, `model_info` defaults, and per-provider constructor details.

## Conventions

- **Env vars**: Always `os.getenv("VAR", "default")`. No third-party env library.
- **Provider secrets**: API keys are read from env only — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_ANTHROPIC_API_KEY`.
- **Provider endpoints**: Azure endpoint URLs are stored per-model in `agent_models.json` under the `endpoint` field. No endpoint env var is used; each Azure resource has its own URL.
- **No Django ORM**: `DATABASES = {}`. Sessions use signed cookies.
- **Runtime state split**: Redis serves four roles — (1) active run coordination (lease per `session_id`, heartbeat, cross-instance cancel signal via `agents/session_coordination.py`); (2) remote-user readiness ephemeral state (status keys, quorum override, deferred readiness latch for next-run blocking after mid-run disconnect); (3) attachment text cache (`{REDIS_NAMESPACE}:attachment:{session_id}:{attachment_id}:text`, TTL `REDIS_ATTACHMENT_TTL_SECONDS`, default 24 h); (4) per-user impersonated export keys (`{NS}:remote_user:{session_id}:{user_name}:export_key` → key UUID, `{NS}:remote_export:key:{key}` → `{session_id, user_name}`, TTL `REDIS_REMOTE_USER_TOKEN_TTL_SECONDS`). MongoDB persists durable discussion history and `agent_state` resume data (no file content). Azure Blob holds raw attachment bytes.
- **Secret key auth**: GET/POST HTMX requests can carry `X-App-Secret-Key`; invalid or missing keys get read-only views or rejected saves.
- **Model catalog**: `agent_models.json` is keyed by model name; Azure deployments use the optional `deployment_name` field (defaults to model key). See [docs/agent_factory.md](agent_factory.md) for schema details.
- **SCSS**: Compiled at request time in dev, offline in production.
- **SCSS style contract**: Follow [docs/scss_style_guide.md](scss_style_guide.md) for token usage, component semantics, and responsive guardrails.
- **Template naming**: Partials in `partials/` subdirectory, prefixed with `_` for includes.
- **Collection name contract**: Never hardcode collection names in feature modules; import collection constants from `server/db.py`.
- **Utility reuse contract**: Reuse shared server helpers from `server/util.py` (`utc_now`, `json_response`, `json_error`, `json_dumps`) instead of duplicating helper behavior in services/views/integration modules.
- **Duplicate conflict contract**: Services catch `DuplicateKeyError`, log a structured warning, and raise a user-safe `ValueError` with clear remediation text.

## Frontend JS Boundaries

- `server/static/server/js/app.js`: shared SPA helpers only (secret header injection, shared helper utilities, generic cross-page hooks).
- `server/static/server/js/chat_copy_utils.js`: shared chat bubble copy behavior and payload formatting.
- `server/static/server/js/chat_surface_utils.js`: shared Home/Remote/Guest chat rendering helpers (attachment rows/chips, file-size labels, icon fallback, generic scroll/history-container helpers, local-time post-render shim).
- `server/static/server/js/markdown_viewer.js`: shared markdown rendering module. Consumed by Home, Trello export popup, Jira export popup, Remote, and Guest. Never duplicate markdown parser logic in feature modules.
- `server/static/server/js/mermaid_viewer.js`: shared Mermaid hydration helper for rendered markdown diagrams.
- `server/static/server/js/project_config.js`: project configuration feature behavior only (agent cards, form state sync, config-page secret gating).
- `server/static/server/js/config_readonly_markdown.js`: config readonly markdown rendering sync for objective/prompts.
- `server/static/server/js/mcp_json_editor.js`: config-page MCP JSON editor lifecycle only (code-mode editor mount/unmount, format/validate controls, textarea sync).
- `server/static/server/js/mcp_oauth.js`: shared MCP OAuth popup/bootstrap helpers used by config/home flows.
- `server/static/server/js/home.js`: home chat feature behavior only (chat runtime UI, SSE rendering, human gate interactions).
- `server/static/server/js/remote_user.js`: remote-user chat page behavior only (chat bubbles, WebSocket lifecycle, export dropdown injection/removal, attachment handling for gate responses).
- `server/static/server/js/guest_user.js`: guest readonly page behavior only (history render, WebSocket updates, copy parity).
- `server/static/server/js/trello_config.js`: Trello project-configuration behavior only (token generation, workspace/board/list cascade, create board/list modal).
- `server/static/server/js/trello.js`: Trello export modal for chat sessions only.
- `server/static/server/js/jira_config.js`: Jira project-configuration behavior only (per-type credential verify, project cascade dropdowns).
- `server/static/server/js/jira.js`: shared Jira export helpers only (schemas, editor rendering, API helpers).
- `server/static/server/js/jira_adapter_factory.js`: shared Jira adapter factory for export modal lifecycle and left-pane behavior.
- `server/static/server/js/jira_software.js`: Jira Software provider registration wrapper only.
- `server/static/server/js/jira_service_desk.js`: Jira Service Desk provider registration wrapper only.
- `server/static/server/js/jira_business.js`: Jira Business provider registration wrapper only.
- `server/static/server/js/export_modal_base.js`: shared export modal shell used by providers.
- `server/static/server/js/provider_registry.js`: provider capability registry used by shared modules to open export modals and sync provider config without hardcoded provider switches.

Keep gate/quorum/run-state decisions in feature modules (`home.js`, `remote_user.js`) and keep guest page readonly-only.

When adding new UI behavior, create a dedicated module for a distinct feature surface instead of extending `app.js`.
See [docs/frontend_js_architecture.md](frontend_js_architecture.md) for the module ownership and event contract.

### Chat Surface ID Naming Convention

Chat surface ids use stable surface-prefixed naming:

- Home: `chat-*`
- Remote: `remote-chat-*`
- Guest: `guest-chat-*`

Canonical container ids:

- Home: `chat-messages`, `chat-history-msgs`
- Remote: `remote-chat-messages`, `remote-chat-history-msgs`
- Guest: `guest-chat-messages`, `guest-chat-history-msgs`

Do not create multiple ids for the same role in one surface. Prefer shared helper
parameterization over id unification refactors.

## Export Provider Architecture

- Shared modules (`home.js`, `project_config.js`) must never hardcode provider names.
- Provider modules self-register capabilities through `window.ProviderRegistry.register("<provider>", capabilities)`.
- Current required capabilities:
	- `openExportModal(context)` for chat export launch.
	- `syncConfigState(context)` for config-page state sync.
- New providers should require only provider-specific module + backend endpoints + docs updates.

## Agent Skills Location

Repo-local extension skills live under `.agents/skills/`.

- `.agents/skills/chat_surface_shared/SKILL.md` — shared chat markup/class/header contracts across Home/Remote/Guest.
- `.agents/skills/chat_compose_attachment_contract/SKILL.md` — compose/send/attachment behavior contracts for Home + Remote.
- `.agents/skills/chat_session_readiness_card/SKILL.md` — readiness card behavior/data-hook contract.
- `.agents/skills/export_popup_base/SKILL.md` — baseline modal structure and lifecycle.
- `.agents/skills/export_provider_adapter/SKILL.md` — adapter contract for provider endpoints.
- `.agents/skills/export_agents_sync/SKILL.md` — export allowlist sync/reset rules on agent rename/remove.
- `.agents/skills/hierarchical_export_items/SKILL.md` — temp_id/parent_temp_id hierarchy and BFS push contracts.
- `.agents/skills/jira_layer_separation/SKILL.md` — mandatory Jira module ownership and split-layer checklist.
- `.agents/skills/key_value_form_pattern/SKILL.md` — standard repeating key/value form-row contract.
- `.agents/skills/markdown_viewer_reuse/SKILL.md` — shared markdown rendering across Home, export modals, and future providers.
- `.agents/skills/mcp_tool_integration/SKILL.md` — MCP schema, runtime wiring, redaction, and transport constraints.
- `.agents/skills/observability_logging/SKILL.md` — logging/tracing contracts for I/O paths.
- `.agents/skills/ui_consistency_guardrails/SKILL.md` — cross-page visual consistency requirements.
- `.agents/skills/scss_style_consistency/SKILL.md` — token-only SCSS and shared component style consistency requirements.
- `.agents/skills/active_session_coordination/SKILL.md` — Redis lease/heartbeat/cancel and Mongo resume-state contract for chat run lifecycle changes.
- `.agents/skills/remote_user_quorum/SKILL.md` — quorum/event contracts and Redis key model.
- `.agents/skills/chat_attachment_workflow/SKILL.md` — attachment upload/bind/Redis-cache/vision/delete contract and implementation checklist.
- `.agents/skills/frontend_shared_utility_reuse/SKILL.md` — shared frontend helper extraction patterns and boundaries.
- `.agents/skills/datetime_storage/SKILL.md` — BSON datetime storage/read/render consistency contract.
- `.agents/skills/remote_user_export/SKILL.md` — per-user impersonated export key lifecycle (Redis schema, auth wiring, host/remote UI event contracts, purge behavior).

## Integration Docs

- [docs/trello_integration.md](trello_integration.md) — Trello auth flow, token lifecycle, export pipeline.
- [docs/jira_integration.md](jira_integration.md) — Jira three-type architecture, credential resolution, ADF format, per-type export_agents, push response shape.
