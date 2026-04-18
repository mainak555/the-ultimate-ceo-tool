# Trello Integration

## Architecture

The Trello integration uses a three-file backend split plus a dedicated JS module:

| File | Responsibility |
|------|---------------|
| `server/trello_client.py` | Pure Trello REST API client — no Django imports |
| `server/trello_service.py` | Business logic — token CRUD, credential resolution, orchestration |
| `server/trello_views.py` | Django views (thin controllers) — JSON API endpoints |
| `server/trello_urls.py` | URL routing — included under `/trello/` prefix |
| `server/static/server/js/trello.js` | Frontend modal — auth flow, cascade dropdowns, extract & push |

## Project Config Schema

```yaml
integrations:
  enabled: true
  export_agent: ""          # blank = show on every message
  trello:
    enabled: true
    app_name: "MyApp"       # required — shown in Trello auth popup
    api_key: "abc..."       # required — masked in UI, stays server-side
    default_workspace: ""   # optional — pre-select in modal
    default_board_name: ""  # optional
    default_list_name: ""   # optional
    export_mapping:
      system_prompt: "..."  # extraction prompt
```

## Session Token Lifecycle

- Tokens are **per chat session**, stored in the `chat_sessions` MongoDB collection
- Fields: `trello_token` (string), `trello_token_expiry` (datetime, UTC)
- Expiry: **1 hour** from authorization
- Token is obtained via Trello's `postMessage` popup flow
- The `api_key` never leaves the server — the frontend only receives the auth URL

## Auth Flow

1. Frontend calls `GET /trello/<session_id>/auth-url/`
2. Backend builds URL: `https://trello.com/1/authorize?expiration=1hour&name=<app_name>&scope=read,write&response_type=token&key=<api_key>&callback_method=fragment&return_url=<callback_url>`
3. Frontend opens popup → user authorizes → Trello redirects popup to `/trello/callback/#token=<token>`
4. Callback page reads hash, relays token to opener via same-origin `postMessage`, then self-closes
5. Frontend sends `POST /trello/<session_id>/store-token/` with `{token: "..."}`
6. Backend stores token + 1-hour expiry on the session document

## API Endpoints

All endpoints require `X-App-Secret-Key` header.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/trello/<sid>/auth-url/` | Get Trello authorization URL |
| `POST` | `/trello/<sid>/store-token/` | Store token (`{token}`) |
| `GET` | `/trello/<sid>/token-status/` | Check token validity → `{valid, expires_at}` |
| `GET` | `/trello/<sid>/workspaces/` | List workspaces → `[{id, displayName}]` |
| `GET` | `/trello/<sid>/boards/?workspace=` | List boards → `[{id, name}]` |
| `GET` | `/trello/<sid>/lists/?board=` | List lists → `[{id, name}]` |
| `POST` | `/trello/<sid>/create-board/` | Create board (`{name, workspace_id?}`) → `{id, name}` |
| `POST` | `/trello/<sid>/create-list/` | Create list (`{name, board_id}`) → `{id, name}` |
| `POST` | `/trello/<sid>/extract/` | Run extraction → `{items: [...]}` |
| `POST` | `/trello/<sid>/push/` | Push cards (`{list_id, items}`) → `{status, result}` |

## Export Flow

1. User clicks "Export to Trello" button on a chat message
2. Modal opens → checks token status
3. If no token → "Authorize" button → popup flow
4. Cascade dropdowns load: Workspace → Board → List (each with "➕ Create New")
5. "Extract Items" button runs the extraction agent
6. Preview shows cards with badges: 📋 Card, 📝 Description, ☑️ Checklist
7. "Export to Trello" pushes items → shows success with card links

## Mapping Structure

```
items: [
  {
    title: "Card title",
    description: "Card description",
    children: [
      { title: "Checklist item 1" },
      { title: "Checklist item 2" }
    ]
  }
]
```

- `title` → Trello Card name
- `description` → Trello Card description
- `children` → Checklist named "Tasks" with check items

## Security

- `api_key` is stored in project config and never sent to the frontend
- All Trello API calls are proxied through Django backend
- Tokens are short-lived (1 hour) and scoped to a single chat session
- All endpoints gated by `X-App-Secret-Key` header
