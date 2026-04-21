# Architecture

## Project Structure

```
product-discovery/
├── agents/              # Root AutoGen runtime package (model factory, team builder)
│   └── integrations/    # Jira/Trello export clients + LLM extractor
├── agent_models.json    # Shared model catalog keyed by model name
├── config/              # Django project package (settings, root URLs, WSGI)
├── server/              # Main Django app
│   ├── db.py            # MongoDB connection singleton (PyMongo)
│   ├── model_catalog.py # Shared model catalog + default prompt loader for Django
│   ├── schemas.py       # Input validation (validate_project, validate_agent)
│   ├── services.py      # Business logic (CRUD, auth verification)
│   ├── views.py         # HTMX view controllers (thin, delegates to services)
│   ├── urls.py          # App URL routing
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
- `validate_human_gate(data)` — validates the optional approval/feedback gate.
- `validate_team(data, human_gate_enabled)` — validates team type and max iterations.
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

### Root `agents/` Package — Runtime Integration
- `agents/config_loader.py` reads the shared `agent_models.json` catalog.
- `agents/factory.py` resolves provider-specific AutoGen model clients from model names.
- `agents/prompt_builder.py` resolves system prompts and appends the project objective.
- `agents/team_builder.py` builds AutoGen teams (`RoundRobinGroupChat` or `SelectorGroupChat`) from saved configuration. The team type is read from `project["team"]["type"]`. Each `AssistantAgent` receives `description=` (line 1 of its resolved system message) so that `SelectorGroupChat`'s `{roles}` placeholder renders meaningful routing context.

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
- **Secret key auth**: GET/POST HTMX requests can carry `X-App-Secret-Key`; invalid or missing keys get read-only views or rejected saves.
- **Model catalog**: `agent_models.json` is keyed by model name; Azure deployments use the optional `deployment_name` field (defaults to model key). See [docs/agent_factory.md](agent_factory.md) for schema details.
- **SCSS**: Compiled at request time in dev, offline in production.
- **Template naming**: Partials in `partials/` subdirectory, prefixed with `_` for includes.

## Frontend JS Boundaries

- `server/static/server/js/app.js`: shared SPA helpers only (secret header injection, shared helper utilities, generic cross-page hooks).
- `server/static/server/js/project_config.js`: project configuration feature behavior only (agent cards, form state sync, config-page secret gating).
- `server/static/server/js/home.js`: home chat feature behavior only (chat runtime UI, SSE rendering, human gate interactions).
- `server/static/server/js/trello_config.js`: Trello project-configuration behavior only (token generation, workspace/board/list cascade, create board/list modal).
- `server/static/server/js/trello.js`: Trello export modal for chat sessions only.
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
- `.agents/skills/ui_consistency_guardrails/SKILL.md` — cross-page visual consistency requirements.
