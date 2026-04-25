# Jira Integration

## Architecture

The Jira integration uses a common facade + per-type services on the backend, and a shared adapter factory + per-type wrappers on the frontend:

| File | Responsibility |
|------|---------------|
| `server/jira_client.py` | Pure Jira REST API client — no Django imports |
| `server/jira_service.py` | Common Jira facade — credential resolution, shared persistence, extraction orchestration, type dispatch |
| `server/jira_software_service.py` | Jira Software type-specific normalization, spaces, and push logic |
| `server/jira_service_desk_service.py` | Jira Service Desk type-specific normalization, spaces, and push logic |
| `server/jira_business_service.py` | Jira Business type-specific normalization, spaces, and push logic |
| `server/jira_views.py` | Django views (thin controllers) — JSON API endpoints |
| `server/jira_urls.py` | URL routing — included under `/jira/` prefix |
| `server/static/server/js/jira_config.js` | Config page Jira settings UX — verify credentials, cascade project dropdowns per type |
| `server/static/server/js/jira.js` | Shared Jira export helpers (`window.JiraUtils`) |
| `server/static/server/js/jira_adapter_factory.js` | Shared Jira export adapter factory (`window.JiraAdapterFactory`) |
| `server/static/server/js/jira_software.js` | Jira Software ProviderRegistry wrapper |
| `server/static/server/js/jira_service_desk.js` | Jira Service Desk ProviderRegistry wrapper |
| `server/static/server/js/jira_business.js` | Jira Business ProviderRegistry wrapper |
| `server/model_catalog.py` | Export prompt hints per Jira type (`jira_export_prompt_hint(type_name)`) |

Jira modules self-register three provider capabilities via `window.ProviderRegistry`:
- `"jira_software"` — Scrum/Kanban board issues
- `"jira_service_desk"` — Service Desk requests
- `"jira_business"` — Business/Work Management tasks

## Three Project Types

Each Jira type is fully independent: separate credentials, separate Jira site, separate export schema, and separate export_agents allowlist.

| Type | Jira API Base | Push Endpoint |
|------|---------------|---------------|
| `software` | `/rest/api/3/` | `POST /rest/api/3/issue` |
| `service_desk` | `/rest/servicedeskapi/` | `POST /rest/servicedeskapi/request` |
| `business` | `/rest/api/3/` | `POST /rest/api/3/issue` |

## Project Config Schema

```yaml
integrations:
  enabled: true
  jira:
    enabled: true
    software:
      enabled: true
      site_url: "https://yoursite.atlassian.net"  # required when enabled
      email: "you@example.com"                     # required when enabled
      api_key: "..."                               # required when enabled; masked in UI
      default_project_key: "PROJ"                  # selected via cascade dropdown
      default_project_name: "My Project"           # display name (stored for readonly view)
      export_agents: []                            # empty = show on every message; list of agent names to restrict
      export_mapping:
        model: ""                                  # blank = fall back to first assistant agent's model
        temperature: 0.0
        system_prompt: "..."                       # extraction prompt
    service_desk:
      # same structure as software
    business:
      # same structure as software
```

**Validation rules** (`schemas.py → validate_jira_type_config`):
- `site_url`, `email`, `api_key` are required when the type is enabled.
- `export_agents` entries must match an existing agent name (case-insensitive check against the project's agent list).
- At least one type must be enabled when `jira.enabled = true`.

## Credential Resolution

- Jira has **no session-level token** — credentials live entirely on the project document.
- Credentials are read from the raw MongoDB document (not the normalized view) to avoid masking.
- `_resolve_type_credentials_from_project(project, type_name)` extracts `(site_url, email, api_key)` for a given type.
- `_resolve_project_type_credentials(project_id, type_name)` is used by config-page (project-scoped) endpoints.
- `_resolve_session_type_credentials(session_id, type_name)` is used by export modal (session-scoped) endpoints.

## Service Layer Separation

- `jira_service.py` is the common layer and public facade imported by `jira_views.py`.
- Type-owned logic must remain in:
  - `jira_software_service.py`
  - `jira_service_desk_service.py`
  - `jira_business_service.py`
- Type modules must not import `jira_service.py`; the facade delegates to type modules.

This keeps shared behavior (credential resolution, extraction orchestration, payload persistence, discussion reference fetch) separate from type-specific behavior (normalization, spaces selection, push implementation).

## Authentication

All Jira API calls use HTTP Basic Auth: `base64("email:api_key")` in the `Authorization` header.

`jira_client._auth_headers(email, api_key)` builds the header dict. The `api_key` never leaves the server.

Credential verification calls `GET /rest/api/3/myself` and returns the Atlassian user profile.

## Config Page Flow

1. User enables Jira integration and enables one or more types.
2. Each type sub-section shows: Site URL, Email, API Token, **Test Connection**, Default Project cascade.
3. "Test Connection" → `GET /jira/project/<pid>/verify/<type>/` — returns `{display_name, email}` on success.
4. After successful verify, the project cascade dropdown loads via `GET /jira/project/<pid>/spaces/<type>/`.
5. Selecting a project populates hidden `default_project_key` / `default_project_name` fields.
6. `jira_config.js` wires all three types on `DOMContentLoaded` and `htmx:afterSwap`.

## API Endpoints

All endpoints require `X-App-Secret-Key` header.
`<type_name>` must be one of `software`, `service_desk`, `business` (validated by `_validate_type()` in jira_views.py).

### Project-scoped (config page — `/jira/project/<pid>/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/jira/project/<pid>/verify/<type>/` | Verify credentials → `{display_name, email}` |
| `GET` | `/jira/project/<pid>/spaces/<type>/` | List Jira projects/service desks → `[{key, name}]` |

### Session-scoped (export modal — `/jira/<sid>/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/jira/<sid>/token-status/<type>/` | Check configuration → `{configured, default_project_key, default_project_name}` |
| `GET` | `/jira/<sid>/spaces/<type>/` | List Jira projects/service desks → `[{key, name}]` |
| `POST` | `/jira/<sid>/extract/<did>/<type>/` | Run extraction agent → `{issues: [...]}` |
| `GET` | `/jira/<sid>/export/<did>/<type>/` | Load saved export payload for discussion |
| `POST` | `/jira/<sid>/export/<did>/<type>/` | Save edited export payload for discussion |
| `GET` | `/jira/<sid>/reference/<did>/` | Raw markdown reference from `discussion.content` (shared across all types) |
| `POST` | `/jira/<sid>/push/<type>/` | Push issues to Jira → `{status, result: [{issue_key, summary, url, warnings}]}` |

## Export Flow

1. User enters Secret Key (export controls are hidden without it).
2. User clicks "Export" on an agent message — `ProviderRegistry` routes to the appropriate Jira type modal.
3. Modal opens → checks configuration status via `/jira/<sid>/token-status/<type>/`.
4. If not configured → message directs user to configure credentials in project settings.
5. Project cascade dropdown loads via `/jira/<sid>/spaces/<type>/` with the saved default pre-selected.
6. Right reference pane loads raw markdown from `/jira/<sid>/reference/<did>/`.
7. "Extract Items" → `POST /jira/<sid>/extract/<did>/<type>/` — runs the LLM extraction agent and populates the left editor workspace.
8. "Save" → `POST /jira/<sid>/export/<did>/<type>/` — persists the edited payload under `discussions[].exports.jira.<type>`.
9. "Export to Jira" → `POST /jira/<sid>/push/<type>/` — creates issues and returns links/warnings.

## Frontend Adapter Separation

- `jira_adapter_factory.js` owns shared export modal adapter behavior.
- `jira_software.js`, `jira_service_desk.js`, and `jira_business.js` stay thin and only register per-type providers.
- Shared modules (`home.js`, `provider_registry.js`, `export_modal_base.js`) remain provider-agnostic.

## Jira Software Hierarchical Export

Jira Software issues are nested. The export modal renders an accordion tree, the payload carries parent linkage via `temp_id` / `parent_temp_id`, and the push walks breadth-first so parents always exist before their children. The shared contract — including duplicate-id defense, cascade delete, BFS push, and parent-failure recovery — lives in [`.agents/skills/hierarchical_export_items/SKILL.md`](../.agents/skills/hierarchical_export_items/SKILL.md). Any new export provider whose items can nest must follow that skill.

### Modal cascade
The left pane shows a two-column cascade: **Project** and **Sprint**. The Sprint dropdown always exposes a `Backlog` option whose value is `""`. Changing either selector cascades to every issue card; per-card overrides remain editable afterwards.

> The Epic dropdown that previously sat on the modal has been removed. Parent linkage is expressed in the issue tree (via `parent_temp_id`), not via a global Epic selector.

Software metadata for the selected destination project also includes an `existing_issues` catalog (`key`, `summary`, `issue_type`, `parent_key`) used to power the per-card **Existing Issue** dropdown (`New` + filtered matches).

### Push behavior (`push_issues_software`)

1. Group items by `parent_temp_id`; identify roots (`parent_temp_id` null or referencing an unknown id).
2. BFS from roots, populating `temp_to_key` after each successful create.
3. If `existing_issue_key` is set on an item, update that existing Jira issue with the edited card fields, set `temp_to_key[temp_id] = existing_issue_key`, append a success-style result row, and continue BFS so descendants can attach to the mapped Jira key.
4. For each newly created child, set `fields.parent = {"key": temp_to_key[parent_temp_id]}`. On a `400` mentioning `parent` or `customfield`, retry once with `customfield_10014` (Epic Link) for company-managed-project compatibility.
5. If a child's parent failed to create, append a warning (`Parent '<id>' was not created; this issue will be created as a root.`) and proceed without a parent reference. Descendants of a failed root are skipped.
6. Result entries echo `temp_id` so the client can correlate.

### Sprint assignment

- `sprint == ""` → **Backlog**. Skip the Agile API call entirely; Jira places new issues in the backlog by default.
- `sprint` non-empty and numeric → `POST /rest/agile/1.0/sprint/{sprintId}/issue` with `{"issues": [issue_key]}` after the issue is created.
- Issue types `Epic` and `Sub-task` are **non-sprintable**; the Agile call is skipped with a warning.
- Sprint API failures are recorded as per-issue warnings, never as a hard batch failure.

## Export Field Schemas

Field schemas are defined in `server/model_catalog.py` and used by extraction prompts.

### Software (`jira_software`)

| Field | Notes |
|-------|-------|
| `temp_id` | Client-stable id used to wire parent / child relations within the batch. Auto-generated if missing. Never sent to Jira. |
| `parent_temp_id` | `temp_id` of the parent item, or `null` for roots. Drives `fields.parent` at push time. |
| `existing_issue_key` | Optional Jira issue key selected in the UI. When present, push updates that Jira issue using current card fields, maps `temp_id` to that key, and continues hierarchy linking for descendants. |
| `summary` | Required; issue title |
| `description` | Plain text; wrapped in ADF by client |
| `issue_type` | Epic \| Feature \| Story \| Task \| Sub-task \| Bug |
| `priority` | Highest \| High \| Medium \| Low \| Lowest |
| `sprint` | Sprint id (string) or `""` for Backlog. Empty value skips the Agile API call. |
| `labels` | List of strings |
| `story_points` | Numeric; omitted if null |
| `components` | List of component names |
| `acceptance_criteria` | Plain text |
| `confidence_score` | 0.0–1.0 |

> Hierarchy is governed by the shared skill `.agents/skills/hierarchical_export_items/SKILL.md`. **Do not** add a `depth_level` field — depth is derived from the `parent_temp_id` chain.

### Service Desk (`jira_service_desk`)

| Field | Notes |
|-------|-------|
| `summary` | Required |
| `description` | Plain text; wrapped in ADF by client |
| `request_type` | Resolved by name against service desk; falls back to first available |
| `priority` | Highest \| High \| Medium \| Low \| Lowest |
| `labels` | List of strings |
| `impact` | Free text |
| `urgency` | Free text |
| `confidence_score` | 0.0–1.0 |

### Business (`jira_business`)

| Field | Notes |
|-------|-------|
| `summary` | Required |
| `description` | Plain text; wrapped in ADF by client |
| `issue_type` | Task \| Milestone \| Sub-task \| Epic |
| `priority` | Highest \| High \| Medium \| Low \| Lowest |
| `labels` | List of strings |
| `due_date` | ISO date string; omitted if blank |
| `category` | Free text |
| `confidence_score` | 0.0–1.0 |

## Atlassian Document Format (ADF)

Jira REST API v3 requires descriptions as Atlassian Document Format (ADF) JSON, not plain text.

`jira_client._adf_doc(text)` wraps a plain text string in the minimal ADF structure:

```json
{
  "version": 1,
  "type": "doc",
  "content": [{"type": "paragraph", "content": [{"type": "text", "text": "..."}]}]
}
```

This is applied automatically in `push_issues_software()`, `push_issues_service_desk()`, and `push_issues_business()`.

## Per-Type Export Agents

Each Jira type has its **own** `export_agents` allowlist, stored at `integrations.jira.<type>.export_agents`.

- `[]` (empty) — show the export button on **every** agent message for that type.
- Non-empty list — restrict the export button to messages from named agents only.
- This is enforced independently for `jira_software`, `jira_service_desk`, and `jira_business`.
- There is **no global** `integrations.jira.export_agents` field — all filtering is per-type.

## Export Payload Storage

Saved payloads are stored on the chat session document under `discussions[].exports`:

```json
{
  "jira": {
    "software": { "schema_version": "...", "updated_at": "ISO", "exported": false, "source": "extract|manual", "issues": [...] },
    "service_desk": { "schema_version": "...", "updated_at": "ISO", "exported": false, "source": "extract|manual", "issues": [...] },
    "business": { "schema_version": "...", "updated_at": "ISO", "exported": false, "source": "extract|manual", "issues": [...] }
  }
}
```

The `reference/<did>/` endpoint always reads from `discussion.content` (the live message), not from the saved payload.

## Model Catalog Integration

`server/model_catalog.py` exposes `jira_export_prompt_hint(type_name: str) -> str` which returns a default extraction system prompt for each type. This is surfaced in the config form as pre-fill hint text for the `export_mapping.system_prompt` textarea. The function returns an empty string for unknown type names.

## Adding a New Jira Type

Jira types are enumerated in `JIRA_TYPES = ("software", "service_desk", "business")` in:
- `server/jira_client.py`
- `server/jira_service.py`
- `server/schemas.py`
- `server/views.py` (via `VALID_JIRA_TYPES` in `jira_views.py`)

To add a new type: extend all four constants, add a normalizer in `jira_service.py`, add a push function in `jira_client.py`, add a prompt constant in `model_catalog.py`, add a sub-fieldset in `config_form.html`, and register a new provider key in `jira.js`.
