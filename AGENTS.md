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

See [docs/scss_style_guide.md](docs/scss_style_guide.md) for:
- Token-only SCSS rules and allowed derivations
- Shared button/form/card/modal styling contracts
- Responsive and export-modal aesthetic guardrails

## Trello Integration

See [docs/trello_integration.md](docs/trello_integration.md) for:
- Architecture (trello_client → trello_service → trello_views + trello.js)
- Project config schema and session token lifecycle
- Auth flow, cascade dropdowns, and export pipeline
- API endpoint reference

## Jira Integration

See [docs/jira_integration.md](docs/jira_integration.md) for:
- Architecture (jira_client → jira_service → jira_views + jira.js + jira_config.js)
- Three independent project types: `software`, `service_desk`, `business`
- Per-type credentials, per-type export_agents, and per-type export schema
- Atlassian Document Format (ADF) requirement and how it is handled
- Config page credential verification and project cascade flow
- Export modal flow and push response shape
- API endpoint reference (project-scoped and session-scoped)

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
14. **Feature ownership is mandatory**: Home, Project Config, Trello, and Jira implementations must stay separated in HTMX templates, JS modules, views, and services. Avoid adding feature-specific logic to shared files.
15. **Provider registry is required for exports**: shared modules must use `server/static/server/js/provider_registry.js` (`window.ProviderRegistry`) instead of hardcoding provider names or provider-specific window globals.
16. **Reusable export modal pattern is mandatory**: all export providers (Trello, Jira, PDF, n8n, future) must use `window.ExportModalBase` (`server/static/server/js/export_modal_base.js`) as the shared modal shell. The base owns the overlay, header ("Export to {label}"), 70/30 split layout, right-pane reference loading, and footer buttons. Providers implement an adapter object; never build a modal overlay DOM structure inside a provider file.
17. **Visual consistency is mandatory across pages**: destructive controls (delete buttons/icons), color-token usage, spacing rhythm, and modal typography must match shared patterns defined in SCSS and docs; provider-specific theming is additive, not divergent.
18. **Extension skills are required for new providers**: follow `.agents/skills/export_popup_base/SKILL.md`, `.agents/skills/export_provider_adapter/SKILL.md`, `.agents/skills/ui_consistency_guardrails/SKILL.md`, `.agents/skills/scss_style_consistency/SKILL.md`, and `.agents/skills/markdown_viewer_reuse/SKILL.md` before implementing a new export provider.
19. **SCSS consistency is mandatory**: all styling changes must follow `docs/scss_style_guide.md` and must not introduce hardcoded color values when shared tokens exist.
20. **Markdown rendering must be reusable**: shared markdown rendering belongs in `server/static/server/js/markdown_viewer.js`; Home, Trello popup, Jira popup, and future providers must consume this common module instead of duplicating parsers.
21. **Jira export_agents are per-type**: for Jira, `export_agents` is scoped to each project type (`integrations.jira.software.export_agents`, etc.). There is no global `integrations.jira.export_agents` field. Validation, normalization, and template rendering must all read from the per-type config.
22. **Textarea fields in config forms must include a `<small class="form-hint">` below them** describing the field's purpose in plain language. The hint must be specific to the field's integration and type (e.g. "Prompt used by the extraction agent to parse the discussion into Jira Software issues."). Never leave a textarea without a hint.
23. **Nested fieldset indentation must be uniform across all nesting levels**: both L1 (`.form-group--nested`) and L2 (`.form-group--nested-l2`) use `margin-left: $space-md`. Do not use `$space-lg` or any larger value for L2, and do not add `margin-top` to nested fieldsets — vertical rhythm is provided by the preceding element's `margin-bottom`. See `docs/scss_style_guide.md` §"Section Fieldsets (Config Form)" rules 5–6.
24. **`export_modal_base.js` is the only modal shell**: all export providers call `window.ExportModalBase.open(ctx, adapter)` and implement the adapter interface defined in `.agents/skills/export_popup_base/SKILL.md`. No provider file may build an overlay DOM with `overlay.innerHTML = ...` for the modal wrapper.
25. **Export modal context must include `projectId`**: the context object passed to `ProviderRegistry.openExportModal()` must always carry `{provider, sessionId, discussionId, secretKey, csrfToken, projectId}`. A missing `projectId` is a defect that must be fixed in `home.js`.