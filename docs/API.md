# API Reference

## URL Routes

All routes are under the `server` app namespace.

| Method | Path | View | Description |
|--------|------|------|-------------|
| `GET` | `/` | `index` | Full chat page (home.html) |
| `GET` | `/projects/` | `configurations_page` | Full configurations page (sidebar + create form preloaded) |
| `GET` | `/projects/list/` | `project_list` | HTMX partial — sidebar project list |
| `GET` | `/projects/new/` | `project_new` | HTMX partial — blank config form |
| `POST` | `/projects/create/` | `project_create` | Create project from config form |
| `GET` | `/projects/<project_id>/` | `project_detail` | HTMX partial — config form or readonly |
| `POST` | `/projects/<project_id>/` | `project_detail` | Update a project |
| `POST` | `/projects/<project_id>/delete/` | `project_delete` | Delete project (blocked if chats exist) |
| `POST` | `/projects/<project_id>/clone/` | `project_clone` | Clone project as `<name> - Copy` |
| `GET` | `/chat/sessions/` | `chat_session_list` | List chat sessions for a project (HTMX partial) |
| `POST` | `/chat/sessions/create/` | `chat_session_create` | Create a chat session |
| `POST` | `/chat/sessions/<session_id>/run/` | `chat_session_run` | Start or continue a run (SSE stream; Redis-coordinated active lease) |
| `POST` | `/chat/sessions/<session_id>/restart/` | `chat_session_restart` | Restart from persisted AutoGen team state |
| `POST` | `/chat/sessions/<session_id>/respond/` | `chat_session_respond` | Human gate decision (continue/stop with optional notes) |
| `POST` | `/chat/sessions/<session_id>/attachments/` | `chat_session_upload_attachments` | Upload chat attachments for a session (multipart) |
| `GET` | `/chat/sessions/<session_id>/attachments/<attachment_id>/content/` | `chat_session_attachment_content` | Inline/download attachment content (used for thumbnails) |
| `POST` | `/chat/sessions/<session_id>/stop/` | `chat_session_stop` | Stop an in-progress run |
| `GET` | `/chat/sessions/<session_id>/readiness/status/` | `chat_session_readiness_status` | Readiness snapshot for configured remote users (online/checked/token status) |
| `POST` | `/chat/sessions/<session_id>/readiness/check/` | `chat_session_readiness_check` | Persist the leader-selected required remote users (`user_ids` repeated form fields) |
| `POST` | `/chat/sessions/<session_id>/readiness/<user_id>/token/` | `chat_session_readiness_token` | Mint or reuse invitation token and return a join URL for one remote user |
| `GET` | `/chat/<session_id>/remote-user/<token>/` | `remote_user_page` | Render remote participant page for invitation token |
| `POST` | `/chat/sessions/<session_id>/remote/heartbeat/` | `chat_session_remote_heartbeat` | Refresh remote participant online presence TTL |
| `POST` | `/chat/sessions/<session_id>/remote/attachments/` | `chat_session_remote_upload_attachments` | Upload attachments from remote participant page |
| `GET` | `/chat/sessions/<session_id>/` | `chat_session_detail` | Load chat history panel for one session |
| `POST` | `/chat/sessions/<session_id>/delete/` | `chat_session_delete` | Delete a chat session |
| `POST` | `/chat/sessions/<session_id>/update/` | `chat_session_update` | Update chat session description |
| `GET` | `/trello/<session_id>/token-status/` | `trello_token_status` | Check token validity |
| `GET` | `/trello/<session_id>/workspaces/` | `trello_workspaces` | List Trello workspaces |
| `GET` | `/trello/<session_id>/boards/` | `trello_boards` | List boards (opt. `?workspace=`) |
| `GET` | `/trello/<session_id>/lists/` | `trello_lists` | List lists (`?board=` required) |
| `POST` | `/trello/<session_id>/create-board/` | `trello_create_board` | Create a new board |
| `POST` | `/trello/<session_id>/create-list/` | `trello_create_list` | Create a new list |
| `POST` | `/trello/<session_id>/extract/<discussion_id>/` | `trello_extract` | Run extraction agent on selected discussion message |
| `GET` | `/trello/<session_id>/export/<discussion_id>/` | `trello_export_data` | Load saved Trello export payload for a discussion |
| `POST` | `/trello/<session_id>/export/<discussion_id>/` | `trello_export_data` | Save edited Trello export payload for a discussion |
| `GET` | `/trello/<session_id>/reference/<discussion_id>/` | `trello_discussion_reference` | Load raw discussion markdown reference (`discussion.content`) |
| `POST` | `/trello/<session_id>/push/` | `trello_push` | Push items to Trello |
| `GET` | `/trello/project/<project_id>/auth-url/` | `trello_project_auth_url` | Get project Trello auth URL |
| `POST` | `/trello/project/<project_id>/store-token/` | `trello_project_store_token` | Store project Trello token |
| `GET` | `/trello/project/<project_id>/token-status/` | `trello_project_token_status` | Check project token |
| `GET` | `/trello/project/<project_id>/workspaces/` | `trello_project_workspaces` | List workspaces (project creds) |
| `GET` | `/trello/project/<project_id>/boards/` | `trello_project_boards` | List boards (project creds) |
| `GET` | `/trello/project/<project_id>/lists/` | `trello_project_lists` | List lists (project creds) |
| `POST` | `/trello/project/<project_id>/create-board/` | `trello_project_create_board` | Create board (project creds) |
| `POST` | `/trello/project/<project_id>/create-list/` | `trello_project_create_list` | Create list (project creds) |

See [docs/trello_integration.md](trello_integration.md) for full Trello integration details.

## Jira Endpoints

`<type>` must be one of `software`, `service_desk`, `business`.

### Session-scoped (export modal — `/jira/<sid>/`)

| Method | Path | View | Description |
|--------|------|------|-------------|
| `GET` | `/jira/<sid>/token-status/<type>/` | `jira_session_status` | Check Jira type configuration → `{configured, default_project_key, default_project_name}` |
| `GET` | `/jira/<sid>/spaces/<type>/` | `jira_session_spaces` | List Jira projects/service desks → `[{key, name}]` |
| `POST` | `/jira/<sid>/extract/<did>/<type>/` | `jira_extract` | Run extraction agent on discussion → `{issues: [...]}` |
| `GET` | `/jira/<sid>/export/<did>/<type>/` | `jira_export_data` | Load saved export payload for discussion |
| `POST` | `/jira/<sid>/export/<did>/<type>/` | `jira_export_data` | Save edited export payload for discussion |
| `GET` | `/jira/<sid>/reference/<did>/` | `jira_reference` | Raw markdown from `discussion.content` (shared across types) |
| `POST` | `/jira/<sid>/push/<type>/` | `jira_push` | Push issues to Jira → `{status, result: [{issue_key, summary, url, warnings, temp_id}]}`. For `type=software` the push is BFS over the parent/child tree (`temp_id` / `parent_temp_id`) and may also assign the issue to a sprint via `/rest/agile/1.0/sprint/{id}/issue` when `sprint` is non-empty. See [`docs/jira_integration.md`](jira_integration.md#jira-software-hierarchical-export). |

Session-scoped Trello/Jira auth (Phase 3):

- Leader/admin requests use `X-App-Secret-Key`.
- Remote export requests from invitation pages may use `X-Remote-Export-Capability` instead (delegated session-scoped token).

Remote user WebSocket transport (Phase 3):

- `WS /ws/chat/<session_id>/remote-user/<token>/`
- Client messages: `heartbeat`, `sync_state`, `submit_reply`
- Server messages: `state`, `ack`, `error`

### Project-scoped (config page — `/jira/project/<pid>/`)

| Method | Path | View | Description |
|--------|------|------|-------------|
| `GET` | `/jira/project/<pid>/verify/<type>/` | `jira_project_verify` | Verify Jira credentials → `{display_name, email}` |
| `GET` | `/jira/project/<pid>/spaces/<type>/` | `jira_project_spaces` | List Jira projects/service desks → `[{key, name}]` |

See [docs/jira_integration.md](jira_integration.md) for full Jira integration details.

## Generic Export Popup Endpoint Pattern

For each provider `<provider>`, follow this endpoint contract under provider namespace:

1. `POST /<provider>/<session_id>/extract/<discussion_id>/` — explicit extraction.
2. `GET /<provider>/<session_id>/export/<discussion_id>/` — load saved payload.
3. `POST /<provider>/<session_id>/export/<discussion_id>/` — save edited payload.
4. `GET /<provider>/<session_id>/reference/<discussion_id>/` — raw markdown reference from `discussion.content`.
5. `POST /<provider>/<session_id>/push/` — provider push/export execution.

This keeps provider behavior consistent while allowing provider-specific payload shape and push response data.

## Request/Response Details

### `POST /projects/<project_id>/`

**Content-Type**: `application/x-www-form-urlencoded` (standard HTML form)

**Required request header for write access**:
- `X-App-Secret-Key` — must match `APP_SECRET_KEY`

**Form fields**:
- `project_name` — string
- `objective` — string
- `agents[0][name]` — string
- `agents[0][model]` — selected model name from `agent_models.json`
- `agents[0][system_prompt]` — textarea string
- `agents[0][temperature]` — float string
- `human_gate[enabled]` — `"on"` if checked
- `human_gate[name]` — string
- `human_gate[quorum]` — `"yes"` | `"first_win"` | `"team_config"` (defaults to `"yes"`)
- `human_gate[remote_users][N][name]` — string (required per non-empty row; names must be unique case-insensitively)
- `human_gate[remote_users][N][description]` — string
- `team[type]` — `round_robin` | `selector`
- `team[max_iterations]` — integer string
- `team[model]` — model name (required when `team[type]=selector`)
- `team[system_prompt]` — routing prompt string; supports `{roles}`, `{history}`, `{participants}` (required when `team[type]=selector`)
- `team[temperature]` — float string (default `0.0`; only used for selector)
- `team[allow_repeated_speaker]` — `"on"` if checked (default on; only used for selector)

Single-assistant chat mode semantics:
- When exactly one assistant is configured, `human_gate[enabled]` is required.
- `team[type]=selector` is invalid with one assistant.
- Any non-empty `human_gate[remote_users]` list is invalid with one assistant.
- Team Setup controls may be hidden in the UI for one-assistant projects; server-side validation remains authoritative.
- On save, the persisted project document may omit the `team` object in one-assistant mode.

Remote-user readiness semantics:
- `human_gate[remote_users]` rows are only meaningful when `human_gate[enabled]` is enabled and the project has at least two assistants.
- Remote-user `id` values are server-derived slugs from `name`; clients submit `name`/`description` fields only.

**Success response**: HTML partial (`config_form.html`) with `HX-Trigger: refreshSidebar`

**Error response**: `<div class="alert alert-error">message</div>` with status 400 or 403

### `POST /projects/<project_id>/delete/`

Deletes a project when no dependent chat sessions exist.

Delete policy:
- If any chat sessions reference the project, deletion is blocked.
- No cascade delete is performed.

Responses:
- `200`: project deleted successfully
- `400`: deletion blocked because dependent chat sessions exist
- `403`: unauthorized (missing/invalid `X-App-Secret-Key`)
- `404`: project not found

Model runtime notes:
- Model provider metadata is sourced from `agent_models.json` in the root.
- Runtime client creation expects provider keys in environment variables: `<PROVIDER>_API_KEY`.
- Azure models additionally require `AZURE_API_URL`.
- For Azure entries, model keys are deployment names.

## MongoDB Collection

**Collection**: `project_settings`

**Schema**:
```json
{
  "project_name": "string (unique)",
  "objective": "string",
  "agents": [
    {
      "name": "string",
      "model": "string",
      "system_prompt": "string",
      "temperature": 0.7
    }
  ],
  "human_gate": {
    "enabled": true,
    "name": "Architect"
  },
  "team": {
    "type": "round_robin | selector",
    "max_iterations": 5,
    "model": "string (selector only)",
    "system_prompt": "string (selector only)",
    "temperature": 0.0,
    "allow_repeated_speaker": true
  }
}
```

## Chat Session State Persistence

`chat_sessions` documents may include persisted AutoGen state:

```json
{
  "project_id": "<project_id>",
  "description": "...",
  "discussions": [],
  "status": "idle | running | awaiting_input | awaiting_oauth | awaiting_remote_users | completed | stopped",
  "pending_remote_users": [
    {"user_id": "partner_ops", "name": "Partner Ops"}
  ],
  "current_round": 0,
  "agent_state": {
    "source": "autogen_team_state",
    "version": "1.0.0",
    "saved_at": "2026-04-20T11:22:33.000000+00:00",
    "state": { "type": "TeamState", "...": "AutoGen payload" }
  }
}
```

`pending_remote_users` is present only while `status` is `awaiting_remote_users`.

Restart endpoint contract:

- `POST /chat/sessions/<session_id>/restart/`
- Body fields:
  - `mode`: `continue_only` or `continue_with_context`
  - `text`: required only for `continue_with_context`
- Behavior:
  - Requires a persisted `agent_state`
  - Session must be `completed` or `stopped`
  - If `load_state()` fails due to schema/version drift, restart stops with an explicit version mismatch error

Human gate response endpoint contract:

- `POST /chat/sessions/<session_id>/respond/`
- Body fields:
  - `action`: `continue` or `stop`
  - `text`: optional note/context (used when `action=continue`)
  - `attachment_ids`: optional repeated values bound to the next resumed user message
- Behavior:
  - `continue`: sets session status to `idle` and returns `{status:"ok", task:"<text>", attachment_ids:[...]}`
  - `stop`: sets session status to `stopped`, evicts the runtime team, returns `{status:"stopped"}`

Run endpoint attachment contract:

- `POST /chat/sessions/<session_id>/run/`
- Body fields:
  - `task`: optional text (required on first run unless attachments are provided)
  - `attachment_ids`: optional repeated values
- Behavior:
  - Non-image attachments: text is extracted lazily (Redis-cached, first call downloads from Azure Blob). Full extracted text is appended to the agent task as an `--- Attachments:` block — no truncation.
  - Image attachments: bytes are downloaded from Azure Blob and passed as `autogen_core.Image` objects inside a `MultiModalMessage` to vision-capable models. Requires `"vision": true` in the model's `agent_models.json` entry.

Attachment upload/content contract:

- `POST /chat/sessions/<session_id>/attachments/`
  - Content-Type: `multipart/form-data`
  - File field: repeated `files`
  - Response: `{status:"ok", attachments:[{id, filename, mime_type, size_bytes, is_image, extension, content_url, thumbnail_url?}]}`
- `GET /chat/sessions/<session_id>/attachments/<attachment_id>/content/`
  - Returns raw file content for inline rendering (image thumbnails) and download/open in a new tab.

Azure storage auth note:

- Attachment blob access uses `AZURE_STORAGE_CONTAINER_SAS_URL` (container SAS URL including query token).
- SAS permissions must cover upload/read/list/delete operations used by the attachment pipeline.

Active run coordination contract:

- `POST /chat/sessions/<session_id>/run/` acquires a Redis lease keyed by
  `session_id` before transitioning to `running`.
- Returns `409` when another worker already owns the active run lease.
- Returns `503` when Redis coordination is unavailable (fail-fast; no run start).
- `POST /chat/sessions/<session_id>/stop/` writes a Redis cancel signal so
  cancellation propagates across workers/pods.
- MongoDB remains the durable source for `discussions` and `agent_state`.

Pre-run gate order (when remote users are configured):
- `POST /chat/sessions/<session_id>/run/` first checks remote-user readiness.
- If required users are not online, returns `409` with
  `{ "status": "awaiting_remote_users", "users": [{"user_id", "name"}, ...] }`.
- Only after readiness clears does run-start evaluate MCP OAuth requirements
  (`status = "awaiting_oauth"` when OAuth is pending).

Run behavior by mode:
- Multi-assistant gated runs pause after each full round and may auto-complete when `current_round` reaches `team.max_iterations`.
- Single-assistant chat mode pauses after each assistant turn and does not use `team.max_iterations` for auto-completion; the human `stop` action terminates the conversation.
