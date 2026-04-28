# Architecture

## Project Structure

```
product-discovery/
├── agents/              # Root AutoGen runtime package (model factory, team builder)
│   └── integrations/    # Jira/Trello export clients + LLM extractor
├── core/                # Shared cross-cutting infrastructure modules
│   └── tracing.py       # OpenTelemetry wiring/helpers used by server + agents
├── agent_models.json    # Shared model catalog keyed by model name
├── config/              # Django project package (settings, root URLs, WSGI)
├── server/              # Main Django app
│   ├── db.py            # MongoDB connection singleton (PyMongo)
│   ├── model_catalog.py # Shared model catalog + default prompt loader for Django
│   ├── schemas.py       # Input validation (validate_project, validate_agent)
│   ├── services.py      # Business logic (CRUD, auth verification)
│   ├── views.py         # HTMX view controllers (thin, delegates to services)
│   ├── urls.py          # App URL routing
│   ├── attachment_service.py # Chat attachment upload, lazy Redis-cache extraction, deletion
│   ├── storage_backends.py  # Pluggable blob storage (Azure Blob default; Strategy pattern)
│   ├── trello_client.py # Pure Trello REST API client
│   ├── trello_service.py# Trello business logic + token lifecycle
│   ├── trello_views.py  # Trello thin view controllers
│   ├── trello_urls.py   # Trello URL routing (included under /trello/)
│   ├── jira_client.py   # Pure Jira REST API client (3 types, ADF wrapper)
│   ├── jira_service.py  # Jira common facade (credential resolution, shared persistence, dispatch)
│   ├── jira_software_service.py      # Jira Software type-specific business logic
│   ├── jira_service_desk_service.py  # Jira Service Desk type-specific business logic
│   ├── jira_business_service.py      # Jira Business type-specific business logic
│   ├── jira_views.py    # Jira thin view controllers
│   ├── jira_urls.py     # Jira URL routing (included under /jira/)
│   ├── templates/server/
│   │   ├── config.html             # Full SPA shell
│   │   └── partials/
│   │       ├── header.html          # Header bar
│   │       ├── sidebar.html         # Project list
│   │       ├── config_form.html     # Create/Edit form
│   │       ├── config_readonly.html # Read-only view
│   │       └── _agent_card.html     # Reusable agent card fragment
│   └── static/server/
│       ├── scss/        # SCSS source (compiled by django-compressor)
│       └── js/          # Client-side JS (agent card dynamics)
├── docs/                # Project documentation
├── .env                 # Environment variables (gitignored)
├── Dockerfile           # Production container
├── requirements.txt     # Python dependencies
└── manage.py            # Django management
```

## Layer Responsibilities

### `db.py` — Data Access
- Provides `get_client()`, `get_db()`, `get_collection(name)`.
- Manages MongoDB connection as a module-level singleton.
- Creates indexes on startup (`project_name` unique index on `project_settings`).

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
- `create_project(data)` — validates, inserts, handles duplicate name errors.
- `update_project(project_id, data)` — validates, replaces existing document.
- `delete_project(project_id)` — deletes only when no dependent chat sessions exist.
- `normalize_project(data)` — adapts old documents to the new nested shape for display.
- `get_available_models()` — returns the sorted model catalog used by the UI.
- `verify_secret_key(key)` — constant-time comparison against `APP_SECRET_KEY`.
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
- `agents/session_coordination.py` owns Redis-backed active-session coordination (run lease, heartbeat, cross-instance cancel signaling).

### Root `core/` Package — Shared Infrastructure
- `core/tracing.py` owns OpenTelemetry setup and helpers (`init_tracing`,
	`traced_function`, `traced_block`, `set_payload_attribute`).
- Shared by both Django app modules in `server/` and agent runtime modules
	in `agents/`.

Provider client resolution in `agents/factory.py` (builder-per-provider pattern):
- `openai`          → `OpenAIChatCompletionClient` — direct OpenAI API
- `anthropic`       → `AnthropicChatCompletionClient` — direct Anthropic API
- `google`          → `OpenAIChatCompletionClient` — Google Gemini (OpenAI-compatible)
- `azure_openai`    → `AzureOpenAIChatCompletionClient` — Azure AI Foundry OpenAI deployment
- `azure_anthropic` → `AnthropicChatCompletionClient` with `base_url` — Anthropic model on Azure AI Foundry

To add a new provider, define a `_build_<name>` function in `agents/factory.py` and add one entry to `_PROVIDER_BUILDERS`.

See [docs/agent_factory.md](agent_factory.md) for the full `agent_models.json` schema, environment variable reference, `model_info` defaults, and per-provider constructor details.

## Conventions

- **Env vars**: Always `os.getenv("VAR", "default")`. No third-party env library.
- **Provider secrets**: API keys are read from env only — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_ANTHROPIC_API_KEY`.
- **Provider endpoints**: Azure endpoint URLs are stored per-model in `agent_models.json` under the `endpoint` field. No endpoint env var is used; each Azure resource has its own URL.
- **No Django ORM**: `DATABASES = {}`. Sessions use signed cookies.
- **Runtime state split**: Redis serves two roles — (1) active run coordination (lease per `session_id`, heartbeat, cross-instance cancel signal via `agents/session_coordination.py`); (2) attachment text cache (`{REDIS_NAMESPACE}:attachment:{session_id}:{attachment_id}:text`, TTL `REDIS_ATTACHMENT_TTL_SECONDS`, default 24 h). MongoDB persists durable discussion history and `agent_state` resume data (no file content). Azure Blob holds raw attachment bytes.
- **Secret key auth**: GET/POST HTMX requests can carry `X-App-Secret-Key`; invalid or missing keys get read-only views or rejected saves.
- **Model catalog**: `agent_models.json` is keyed by model name; Azure deployments use the optional `deployment_name` field (defaults to model key). See [docs/agent_factory.md](agent_factory.md) for schema details.
- **SCSS**: Compiled at request time in dev, offline in production.
- **SCSS style contract**: Follow [docs/scss_style_guide.md](scss_style_guide.md) for token usage, component semantics, and responsive guardrails.
- **Template naming**: Partials in `partials/` subdirectory, prefixed with `_` for includes.

## Frontend JS Boundaries

- `server/static/server/js/app.js`: shared SPA helpers only (secret header injection, shared helper utilities, generic cross-page hooks).
- `server/static/server/js/project_config.js`: project configuration feature behavior only (agent cards, form state sync, config-page secret gating).
- `server/static/server/js/home.js`: home chat feature behavior only (chat runtime UI, SSE rendering, human gate interactions).
- `server/static/server/js/trello_config.js`: Trello project-configuration behavior only (token generation, workspace/board/list cascade, create board/list modal).
- `server/static/server/js/trello.js`: Trello export modal for chat sessions only.
- `server/static/server/js/jira_config.js`: Jira project-configuration behavior only (per-type credential verify, project cascade dropdowns).
- `server/static/server/js/jira.js`: shared Jira export helpers only (schemas, editor rendering, API helpers).
- `server/static/server/js/jira_adapter_factory.js`: shared Jira adapter factory for export modal lifecycle and left-pane behavior.
- `server/static/server/js/jira_software.js`: Jira Software provider registration wrapper only.
- `server/static/server/js/jira_service_desk.js`: Jira Service Desk provider registration wrapper only.
- `server/static/server/js/jira_business.js`: Jira Business provider registration wrapper only.
- `server/static/server/js/provider_registry.js`: provider capability registry used by shared modules to open export modals and sync provider config without hardcoded provider switches.

When adding new UI behavior, create a dedicated module for a distinct feature surface instead of extending `app.js`.
See [docs/frontend_js_architecture.md](frontend_js_architecture.md) for the module ownership and event contract.

## Export Provider Architecture

- Shared modules (`home.js`, `project_config.js`) must never hardcode provider names.
- Provider modules self-register capabilities through `window.ProviderRegistry.register("<provider>", capabilities)`.
- Current required capabilities:
	- `openExportModal(context)` for chat export launch.
	- `syncConfigState(context)` for config-page state sync.
- New providers should require only provider-specific module + backend endpoints + docs updates.

## Agent Skills Location

Repo-local extension skills live under `.agents/skills/`.

- `.agents/skills/export_popup_base/SKILL.md` — baseline modal structure and lifecycle.
- `.agents/skills/export_provider_adapter/SKILL.md` — adapter contract for provider endpoints.
- `.agents/skills/jira_layer_separation/SKILL.md` — mandatory Jira module ownership and split-layer checklist.
- `.agents/skills/markdown_viewer_reuse/SKILL.md` — shared markdown rendering across Home, export modals, and future providers.
- `.agents/skills/ui_consistency_guardrails/SKILL.md` — cross-page visual consistency requirements.
- `.agents/skills/scss_style_consistency/SKILL.md` — token-only SCSS and shared component style consistency requirements.
- `.agents/skills/active_session_coordination/SKILL.md` — Redis lease/heartbeat/cancel and Mongo resume-state contract for chat run lifecycle changes.
- `.agents/skills/chat_attachment_workflow/SKILL.md` — attachment upload/bind/Redis-cache/vision/delete contract and implementation checklist.

## Integration Docs

- [docs/trello_integration.md](trello_integration.md) — Trello auth flow, token lifecycle, export pipeline.
- [docs/jira_integration.md](jira_integration.md) — Jira three-type architecture, credential resolution, ADF format, per-type export_agents, push response shape.
