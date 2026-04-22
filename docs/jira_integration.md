# Jira Integration

## Architecture

The Jira integration uses the same three-file backend split as Trello, plus two dedicated JS modules:

| File | Responsibility |
|------|---------------|
| `server/jira_client.py` | Pure Jira REST API client — no Django imports |
| `server/jira_service.py` | Business logic — credential resolution, extraction, push orchestration |
| `server/jira_views.py` | Django views (thin controllers) — JSON API endpoints |
| `server/jira_urls.py` | URL routing — included under `/jira/` prefix |
| `server/static/server/js/jira_config.js` | Config page Jira settings UX — verify credentials, cascade project dropdowns per type |
| `server/static/server/js/jira.js` | Chat export modal — registers 3 ProviderRegistry providers |
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
8. "Save" → `POST /jira/<sid>/export/<did>/<type>/` — persists the edited payload under `discussions[].exports.jira_<type>`.
9. "Export to Jira" → `POST /jira/<sid>/push/<type>/` — creates issues and returns links/warnings.

## Export Field Schemas

Field schemas are defined in `server/model_catalog.py` and used by extraction prompts.

### Software (`jira_software`)

| Field | Notes |
|-------|-------|
| `summary` | Required; issue title |
| `description` | Plain text; wrapped in ADF by client |
| `issue_type` | Story \| Bug \| Task \| Epic \| Subtask |
| `priority` | Highest \| High \| Medium \| Low \| Lowest |
| `labels` | List of strings |
| `story_points` | Numeric; omitted if null |
| `components` | List of component names |
| `acceptance_criteria` | Plain text |
| `confidence_score` | 0.0–1.0 |

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
  "jira_software":    { "issues": [...], "saved_at": "ISO", "source": "extract|manual" },
  "jira_service_desk": { "issues": [...], "saved_at": "ISO", "source": "extract|manual" },
  "jira_business":    { "issues": [...], "saved_at": "ISO", "source": "extract|manual" }
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
