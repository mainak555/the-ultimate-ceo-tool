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
- Architecture (jira_client → jira_service facade + type services + jira_views + jira.js + jira_adapter_factory + jira_config.js)
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
18. **Extension skills are required for new providers**: follow `.agents/skills/export_popup_base/SKILL.md`, `.agents/skills/export_provider_adapter/SKILL.md`, `.agents/skills/ui_consistency_guardrails/SKILL.md`, `.agents/skills/scss_style_consistency/SKILL.md`, `.agents/skills/markdown_viewer_reuse/SKILL.md`, `.agents/skills/hierarchical_export_items/SKILL.md` (when items can nest), and `.agents/skills/observability_logging/SKILL.md` before implementing a new export provider.
19. **SCSS consistency is mandatory**: all styling changes must follow `docs/scss_style_guide.md` and must not introduce hardcoded color values when shared tokens exist.
20. **Markdown rendering must be reusable**: shared markdown rendering belongs in `server/static/server/js/markdown_viewer.js`; Home, Trello popup, Jira popup, and future providers must consume this common module instead of duplicating parsers.
21. **Jira export_agents are per-type**: for Jira, `export_agents` is scoped to each project type (`integrations.jira.software.export_agents`, etc.). There is no global `integrations.jira.export_agents` field. Validation, normalization, and template rendering must all read from the per-type config.
22. **Textarea fields in config forms must include a `<small class="form-hint">` below them** describing the field's purpose in plain language. The hint must be specific to the field's integration and type (e.g. "Prompt used by the extraction agent to parse the discussion into Jira Software issues."). Never leave a textarea without a hint.
23. **Nested fieldset indentation must be uniform across all nesting levels**: both L1 (`.form-group--nested`) and L2 (`.form-group--nested-l2`) use `margin-left: $space-md` and `padding-right: $space-sm`. Do not use `$space-lg` or any larger value for L2, and do not add `margin-top` to nested fieldsets — vertical rhythm is provided by the preceding element's `margin-bottom`. Missing `padding-right` causes textarea scrollbars and inputs to clip at the section edge. See `docs/scss_style_guide.md` §"Section Fieldsets (Config Form)" rules 4–6.
24. **`export_modal_base.js` is the only modal shell**: all export providers call `window.ExportModalBase.open(ctx, adapter)` and implement the adapter interface defined in `.agents/skills/export_popup_base/SKILL.md`. No provider file may build an overlay DOM with `overlay.innerHTML = ...` for the modal wrapper.
25. **Export modal context must include `projectId`**: the context object passed to `ProviderRegistry.openExportModal()` must always carry `{provider, sessionId, discussionId, secretKey, csrfToken, projectId}`. A missing `projectId` is a defect that must be fixed in `home.js`.
26. **Jira backend separation is mandatory**: `server/jira_service.py` is the shared facade only. Type-owned logic must live in `server/jira_software_service.py`, `server/jira_service_desk_service.py`, and `server/jira_business_service.py`.
27. **Jira frontend adapter separation is mandatory**: shared Jira export modal lifecycle logic belongs in `server/static/server/js/jira_adapter_factory.js`; type-owned provider registration belongs only in `jira_software.js`, `jira_service_desk.js`, and `jira_business.js`.
28. **Jira layering skill is required for Jira refactors**: follow `.agents/skills/jira_layer_separation/SKILL.md` before changing Jira services or Jira export adapters.
29. **Jira Software export modal metadata flow is mandatory**: project-specific dropdown options (issue type, priority, sprint, epic) must come from `GET /jira/<session_id>/metadata/software/?project_key=<key>` and must be fetched via `jira_adapter_factory.js` after destination project selection.
30. **Jira Software global Sprint/Epic cascade is mandatory**: destination-level Sprint and Epic selectors define default values for all issue rows and must overwrite all rows whenever the global selector changes; row-level selectors remain editable for per-issue overrides after propagation.
31. **Jira Software fallback behavior is mandatory**: if project metadata cannot be loaded, the modal must remain usable by falling back to default issue type/priority options and minimal Sprint/Epic options (`Backlog`/`None`), with a non-blocking status message.
32. **Jira export label contract is mandatory**: `export_modal_base.js` header title uses adapter `label`, while export button text may use adapter `pushLabel` override. Jira Software currently uses header label `Jira` and push label `Jira Software`.
33. **Export popup add-action button style is mandatory**: left-pane create/add actions for cards/issues/items must use shared class `export-modal__context-add-btn` so all providers keep the same contextual add-button color treatment.
34. **Export popup card background contract is mandatory**: editable item cards across providers must use token-derived light panel backgrounds (for example `lighten($color-bg, 1.5%)`) with shared border/radius rhythm; avoid provider-specific hardcoded card background colors.
35. **Export popup item heading count badge is mandatory**: editable item section headings must use concise labels with a shared count badge (`export-modal__count-badge`), e.g. `Cards <count>`, `Issues <count>`, so counts are shown consistently across providers.
36. **Jira connection status message contract is mandatory**: the Jira export modal connection row must display `{Jira type label} Connected` on success (for example `Jira Software Connected`) and a clear type-scoped error message on failure.
37. **Jira metadata option deduplication is mandatory**: issue-type/priority dropdown options must be deduplicated by display label before rendering so duplicate labels (for example `Epic`) are never shown twice in issue cards.
38. **Project Config readonly markdown contract is mandatory**: readonly Objective, assistant system prompts, team selector system prompt, and integration extraction prompts (Trello and Jira types) must render via `window.MarkdownViewer.render()` using markdown target containers, not plain `<p>`/`<pre>` text blocks.
39. **Project Config readonly integrations parity is mandatory**: when integrations are enabled, readonly view must render Trello plus each enabled Jira type section (`software`, `service_desk`, `business`) with type-scoped readonly fields and extraction prompt visibility.
40. **Hierarchical export items use temp_id + parent_temp_id only**: any export provider whose items can nest (currently Jira Software, plus future providers) must persist parent linkage via `temp_id` + `parent_temp_id` and must NOT store `depth_level`. Depth is derived at render and push time. See `.agents/skills/hierarchical_export_items/SKILL.md` for the full data, render, and push contracts.
41. **Jira Software left-pane cascade is Project + Sprint only**: the Epic dropdown has been removed from the export modal. Parent linkage is expressed via the issue tree (`parent_temp_id`), not via a global Epic selector. The Sprint selector must always include a `Backlog` option whose value is the empty string.
42. **Jira Software push must be BFS with `temp_to_key` mapping**: `push_issues_software` walks roots first then breadth-first, populates `temp_to_key` as each issue is created, and resolves child `parent_temp_id` to a real Jira key before sending `fields.parent`. A child whose parent failed to create must record a warning and be created as a root, never abort the batch. Result entries must echo `temp_id`.
43. **Jira Software sprint assignment via Agile API**: a non-empty `sprint` value triggers `POST /rest/agile/1.0/sprint/{id}/issue` AFTER the issue is created. An empty `sprint` value means Backlog and the Agile API call must be skipped entirely (the issue lands in backlog by default). Sprint failures are per-issue warnings, never batch failures. Issue types `Epic` and `Sub-task` are non-sprintable and must skip the Agile call with a warning.
44. **Structured logging is mandatory**: every Python module that performs HTTP, MongoDB, file, or LLM I/O must declare `logger = logging.getLogger(__name__)` and emit JSON-formatted events through the `LOGGING` config in `config/settings.py`. Do not use `print()` for diagnostics. Event names use dotted snake_case scoped by layer (e.g. `trello.api.call`, `agents.model_client.created`). App logs are currently console-only. If future work adds OpenTelemetry adapters for app telemetry, use a separate exporter pipeline and never route app telemetry to Langfuse. AutoGen payload-event loggers (`autogen_core.events`, `autogen_agentchat.events`) must not emit INFO prompt/tool payload dumps to console; INFO payload events are allowed only through the trace bridge to Langfuse, and console visibility remains ERROR-only. See `.agents/skills/observability_logging/SKILL.md`.
45. **Secret redaction in logs is mandatory**: never log API keys, OAuth tokens, Authorization/Basic-auth headers, passwords, `X-App-Secret-Key`, or full request/response bodies. Strip Trello `key=` and `token=` query parameters from URLs before logging. Body snippets, when needed, must be capped at 500 characters and sanitized.
46. **Request ID propagation is mandatory**: `server/middleware.py` `RequestIdMiddleware` runs at the top of `MIDDLEWARE`, reads `X-Request-ID` (or generates a UUID), binds it into a `contextvars.ContextVar` defined in `server/logging_utils.py`, echoes it back on responses, and clears it in `finally`. `RequestIdFilter` injects the current value on every log record. Async tasks awaited within a request inherit it automatically.
47. **Langfuse tracing for Agent/LLM spans is mandatory**: AutoGen model client and team execution must be observable via the OpenTelemetry exporter wired in `agents/tracing.py`. Wiring is env-gated by `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST`; missing keys must degrade silently with a `tracing.disabled` info log and no exporter errors. `init_tracing()` is invoked exactly once from `server/apps.py` `ServerConfig.ready()`. Export boundary is strict: not all OpenTelemetry data is sent to Langfuse. Only Agent/LLM spans are sent to Langfuse; generic app/framework spans are filtered out. Trace payload contract is canonical: use `input.value` / `output.value` with inferred `input.mime_type` / `output.mime_type` (`application/json`, `text/markdown`, `text/plain`) and avoid duplicate payload copies under parallel keys. Do: keep one raw bridge payload in `langfuse.observation.metadata.autogen_event_raw` while canonical fields carry the primary payload. Don't: duplicate payloads under both canonical and legacy keys (`input.value` + `gen_ai.input`, `output.value` + `gen_ai.output`) or force a single MIME type for all content.
48. **Observability skill is required for new I/O code**: before adding a new HTTP client, service module, MongoDB access path, or agent-runtime entry point, follow `.agents/skills/observability_logging/SKILL.md` for the logger, event-name, redaction, request-ID, and tracing contracts.