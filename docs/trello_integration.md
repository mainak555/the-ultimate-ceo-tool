# Trello Integration

## Architecture

The Trello integration uses a three-file backend split plus a dedicated JS module:

| File | Responsibility |
|------|---------------|
| `server/trello_client.py` | Pure Trello REST API client — no Django imports |
| `server/trello_service.py` | Business logic — token CRUD, credential resolution, orchestration |
| `server/trello_views.py` | Django views (thin controllers) — JSON API endpoints |
| `server/trello_urls.py` | URL routing — included under `/trello/` prefix |
| `server/static/server/js/provider_registry.js` | Provider capability registry used by shared modules |
| `server/static/server/js/trello_config.js` | Config page Trello settings UX — token auth flow, cascade defaults, create board/list |
| `server/static/server/js/trello.js` | Chat export modal — destination picker, extract preview, push to Trello |

Trello modules register provider capabilities via `window.ProviderRegistry` to avoid hardcoded provider switches in shared modules.

## Project Config Schema

```yaml
integrations:
  enabled: true
  trello:
    enabled: true
    export_agents: []              # empty = show on every message; list of agent names to restrict
    app_name: "MyApp"              # required — shown in Trello auth popup
    api_key: "abc..."              # required — masked in UI, stays server-side
    token: "trello-token..."       # generated via config page, expiration=never, masked in UI
    token_generated_at: "ISO str"  # UTC datetime when token was generated
    default_workspace_id: ""       # Trello workspace ID — selected via cascade dropdown
    default_workspace_name: ""     # display name (stored for readonly view)
    default_board_id: ""           # Trello board ID
    default_board_name: ""         # display name
    default_list_id: ""            # Trello list ID
    default_list_name: ""          # display name
    export_mapping:
      model: ""                    # blank = fall back to first assistant agent's model
      temperature: 0.0             # extraction sampling temperature (0.0 = deterministic)
      system_prompt: "..."         # extraction prompt
```

## Project Token Lifecycle

- Tokens are **per project**, stored in `project_settings.integrations.trello.token`
- Fields: `token` (string, masked via `SECRET_MASK`), `token_generated_at` (ISO datetime string, UTC)
- Expiry: **never** — tokens persist until regenerated
- Token section is **always visible** when Trello integration is enabled (both create and edit modes)
- In **create mode**: token textbox shows "Not generated", Generate button is disabled, hint reads "Save the Configuration first to generate the token"
- In **edit mode** (after project save): Generate button is enabled (gated by secret key), token textbox shows `••••••••` with generated datetime once a token exists
- The callback flow stores the token directly in the DB — on completion, the textbox updates to `••••••••` and shows the generated datetime without a page reload
- The `api_key` never leaves the server — the frontend only receives the auth URL
- Legacy session-level token functions (`store_session_token`, etc.) are kept for backward compatibility
- Session-scoped endpoints (`/trello/<sid>/...`) resolve credentials via the session's project token

## Auth Flow (Config Page)

1. Token section is visible on the config page in both create and edit modes
2. In create mode the Generate button is disabled with the hint "Save the Configuration first to generate the token"
3. Once the configuration is saved (project gets a `project_id`), the Generate button becomes enabled
4. User clicks "Generate Token" → frontend calls `GET /trello/project/<project_id>/auth-url/`
5. Backend builds URL: `https://trello.com/1/authorize?expiration=never&name=<app_name>&scope=read,write&response_type=token&key=<api_key>&callback_method=fragment&return_url=<callback_url>`
6. Frontend opens popup → user authorizes → Trello redirects popup to `/trello/callback/?pid=<project_id>&skey=<secret_key>#token=<token>`
7. Callback page reads hash, sends `POST /trello/project/<pid>/store-token/` with `{token: "..."}`, then sends `postMessage("trello_token_stored")` to opener
8. Backend stores token + `token_generated_at` on the project document
9. Frontend receives `postMessage` (or detects popup close), calls `GET /trello/project/<pid>/token-status/` to refresh UI
10. Token textbox updates to `••••••••`, hint shows "Generated: <datetime>", cascade dropdowns (workspace → board → list) become available
11. When the configuration is reloaded in edit mode, a previously generated token displays as `••••••••` with its generated datetime

## API Endpoints

All endpoints require `X-App-Secret-Key` header.

### Session-scoped (export modal — `/trello/<sid>/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/trello/<sid>/auth-url/` | Get Trello authorization URL (legacy) |
| `POST` | `/trello/<sid>/store-token/` | Store session Trello token (legacy) |
| `GET` | `/trello/<sid>/token-status/` | Check token validity → `{valid, token_generated_at, defaults}` |
| `GET` | `/trello/<sid>/workspaces/` | List workspaces → `[{id, displayName}]` |
| `GET` | `/trello/<sid>/boards/?workspace=` | List boards → `[{id, name}]` |
| `GET` | `/trello/<sid>/lists/?board=` | List lists → `[{id, name}]` |
| `POST` | `/trello/<sid>/create-board/` | Create board (`{name, workspace_id?}`) → `{id, name}` |
| `POST` | `/trello/<sid>/create-list/` | Create list (`{name, board_id}`) → `{id, name}` |
| `POST` | `/trello/<sid>/extract/<discussion_id>/` | Run extraction on selected message → `{items: [...]}` |
| `GET` | `/trello/<sid>/export/<discussion_id>/` | Load saved export payload for discussion |
| `POST` | `/trello/<sid>/export/<discussion_id>/` | Save edited export payload for discussion |
| `GET` | `/trello/<sid>/reference/<discussion_id>/` | Load raw markdown reference from `discussion.content` |
| `POST` | `/trello/<sid>/push/` | Push cards (`{list_id, items}`) → `{status, result}` |

### Project-scoped (config page — `/trello/project/<pid>/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/trello/project/<pid>/auth-url/` | Get Trello authorization URL (expiration=never) |
| `POST` | `/trello/project/<pid>/store-token/` | Store project token (`{token}`) → `{status, token_generated_at}` |
| `GET` | `/trello/project/<pid>/token-status/` | Check project token → `{valid, token_generated_at}` |
| `GET` | `/trello/project/<pid>/workspaces/` | List workspaces using project credentials |
| `GET` | `/trello/project/<pid>/boards/?workspace=` | List boards using project credentials |
| `GET` | `/trello/project/<pid>/lists/?board=` | List lists using project credentials |
| `POST` | `/trello/project/<pid>/create-board/` | Create board (`{name, workspace_id?}`) |
| `POST` | `/trello/project/<pid>/create-list/` | Create list (`{name, board_id}`) |

## Export Flow

1. User enters Secret Key (export controls are hidden without it), then clicks "Export" and selects "Trello" on a chat message
2. Modal opens → checks token status via session endpoint (resolves to project token)
3. If no token → message directs user to configure token in project settings
4. Cascade dropdowns load with defaults pre-selected from project config
5. Right reference pane loads raw markdown from `discussion.content` (not from saved export payload)
6. "Extract Items" runs extraction explicitly and updates only the editable export workspace
7. "Save" persists edited Trello payload under `discussions[].exports.trello`
8. "Export to Trello" pushes current edited payload and returns card links/warnings

## Reusable Export Popup Alignment

Trello is the baseline implementation for the shared export popup pattern used by future providers:

1. Left pane = provider export workspace.
2. Right pane = raw markdown reference from `discussion.content`.
3. Footer = Extract, Save, Export, Cancel.
4. Extract and Save are independent actions.

## Extraction Schema Contract

The Trello extraction prompt must return a JSON array (not an object wrapper):

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

Normalization behavior in `server/trello_service.py`:

- `card_title` is required for authored prompts; empty values normalize to `Untitled`
- `card_description` defaults to empty string
- `checklists[].name` defaults to `Tasks`
- empty checklist item titles are dropped
- Trello custom field definition types are `text`, `number`, `date`, `checkbox`, and `list` (per Atlassian Trello docs)
- current exporter normalization stores `custom_fields[].field_type` as `text`
- labels are case-insensitive deduplicated
- `confidence_score` is clamped to `0.0-1.0`

Legacy compatibility:

- Legacy keys `title`, `description`, and `children` are still accepted and normalized
- `children` is converted to a single checklist named `Tasks`

## Saved Export Payload Contract

Saved payload path: `discussions[].exports.trello`

```json
{
  "schema_version": "2026-04-21",
  "updated_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
  "exported": false,
  "source": "extract|manual",
  "cards": [
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
  ],
  "last_push": {
    "pushed_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
    "list_id": "string",
    "result": []
  }
}
```

## Security

- `api_key` and `token` are stored in project config and masked via `SECRET_MASK` in the UI
- All Trello API calls are proxied through Django backend
- Tokens have `expiration=never` and are scoped to the project (shared across sessions)
- All endpoints gated by `X-App-Secret-Key` header
- Token can be regenerated at any time from the config page
