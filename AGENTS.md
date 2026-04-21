# AGENTS.md — Development Instructions

## Overview

Product Discovery is a Django SPA for managing AutoGen agent configurations.
It uses HTMX for partial page updates, SCSS for styling, and PyMongo for MongoDB persistence.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for:
- Project structure and directory layout
- Layer responsibilities (db → schemas → services → views → templates)
- Root `agents/` runtime package responsibilities
- Conventions and coding standards

## API Reference

See [docs/API.md](docs/API.md) for:
- All URL routes and HTTP methods
- Request/response formats
- HTMX partial swap patterns

## UI & Templates

See [docs/UI.md](docs/UI.md) for:
- Page layout and HTMX interaction flow
- Template hierarchy (base → partials)
- CSS class naming conventions

## Trello Integration

See [docs/trello_integration.md](docs/trello_integration.md) for:
- Architecture (trello_client → trello_service → trello_views + trello.js)
- Project config schema and session token lifecycle
- Auth flow, cascade dropdowns, and export pipeline
- API endpoint reference

## Agent Teams & Runtime

See [docs/agent_teams.md](docs/agent_teams.md) for:
- `RoundRobinGroupChat` vs `SelectorGroupChat` — when to use each
- Selector prompt placeholders (`{roles}`, `{history}`, `{participants}`)
- How project `objective` is injected into agent prompts and the selector prompt
- Human gate state machine and approve/feedback resume flow
- Runtime team cache lifecycle (`runtime.py`)
- How to add a new team type end-to-end

## Key Rules

1. **Business logic lives in `server/services.py`** — views are thin controllers
2. **Validation lives in `server/schemas.py`** — returns clean dicts or raises `ValueError`
3. **MongoDB access lives in `server/db.py`** — singleton connection, no ORM
4. **Model catalog lives in `agent_models.json`** — model names are the UI key and are always displayed ascending
5. **AutoGen runtime code belongs in root `agents/`** — keep provider/client logic out of `server/`
6. **No Django ORM** — `DATABASES = {}`, sessions use signed cookies
7. **`APP_SECRET_KEY`** gates write access — HTMX requests send it as `X-App-Secret-Key`
8. **All env vars** read via `os.getenv()` with sensible defaults
9. **Templates** use HTMX partials pattern: full page loads `config.html`, subsequent interactions swap partials into `#main-content` or `#sidebar-list`
10. **SCSS** compiled by django-compressor + django-libsass
11. **No test suite yet** — planned for a future phase
12. **Project deletion safety**: never cascade delete chats when deleting a project. If any chat sessions exist for a project, deletion must be blocked with a clear error message.
13. **Common layer remains common**: global/shared modules (for example `server/static/server/js/app.js`) may contain only cross-feature utilities and hooks.
14. **Feature ownership is mandatory**: Home, Project Config, and Trello implementations must stay separated in HTMX templates, JS modules, views, and services. Avoid adding feature-specific logic to shared files.
15. **Provider registry is required for exports**: shared modules must use `server/static/server/js/provider_registry.js` (`window.ProviderRegistry`) instead of hardcoding provider names or provider-specific window globals.
16. **Reusable export modal pattern is mandatory**: all export providers (Trello, Jira, PDF, n8n, future) must keep the same baseline layout and lifecycle: left editor workspace, right raw markdown pane from `discussion.content`, and footer actions for Extract, Save, and Export.
17. **Visual consistency is mandatory across pages**: destructive controls (delete buttons/icons), color-token usage, spacing rhythm, and modal typography must match shared patterns defined in SCSS and docs; provider-specific theming is additive, not divergent.
18. **Extension skills are required for new providers**: follow `.agents/skills/export_popup_base/SKILL.md`, `.agents/skills/export_provider_adapter/SKILL.md`, and `.agents/skills/ui_consistency_guardrails/SKILL.md` before implementing a new export provider.