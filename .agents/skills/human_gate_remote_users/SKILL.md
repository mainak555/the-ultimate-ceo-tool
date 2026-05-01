---
name: human-gate-remote-users
description: Use when adding, changing, or reviewing the Human Gate `remote_users` list, the `quorum` enum, or any UI/validation/runtime that touches multi-user collaboration on top of the existing Human Gate. Enforces single-assistant restriction, slug-derived `id` (same rules as agent names), legacy-bool migration, key/value form pattern reuse, and the leader-vs-remote split.
---

# Human Gate — Remote Users (collaborative)

The single-user Human Gate has been extended into a multi-user collaborative
contract. The local user (the person running this app) is the **session
leader**: they own MCP authorizations and start every run. **Remote users**
only join an active chat session via a per-session join URL and may respond at
the Human Gate.

Phase 1 ships configuration only. Runtime remote-response collection (Redis
hash → N appended `discussions[].role="user"` entries before the next
`team.run_stream`) is delivered in Phase 3.

---

## Schema (Mongo `project_settings.human_gate`)

```python
human_gate = {
    "enabled": bool,
    "name": str,            # required when enabled
    "quorum": str,          # "yes" | "first_win" | "team_config", default "yes"
    "remote_users": [       # default []
        {
            "id": str,          # SLUG derived from `name` (same sanitisation as
                                # agent names: spaces/hyphens → '_', non-word stripped,
                                # must be a Python identifier). Stable across saves
                                # *as long as `name` is unchanged*. Renaming a user
                                # changes the slug — treat that as a new participant.
            "name": str,        # required, non-empty, unique within list, displayed verbatim
            "description": str, # plain-language role; used by Selector routing
        }
        # Per-user enable/disable is a *runtime* concern (lobby/readiness in
        # Phase 2) and is NOT a stored config field.
    ],
}
```

### Reset rules (mandatory)

- When `enabled = false`: `name = ""`, `remote_users = []`, `quorum = "yes"`.
- When `len(agents) == 1` (single-assistant): a non-empty `remote_users` list
  is **rejected** with `ValueError`. Selector routing is invalid for one
  assistant; remote collaboration requires a multi-agent team.

### Legacy migration

Older documents may carry `quorum: bool`. Migrate on read in both
`server/services.py::normalize_project()` and
`server/schemas.py::validate_human_gate()`:

- `True  → "yes"`
- `False → "first_win"`

Unknown enum values from API/import paths must raise `ValueError` from
`validate_human_gate`. Unknown values found in stored documents must be
silently coerced to `"yes"` in `normalize_project` (read-side defensive
default).

---

## Form contract (`config_form.html`)

The Remote Users block lives **inside** `#human-gate-fields` so it inherits
the gate-disabled hide behavior, plus its own `id="human-gate-remote-block"`
that is hidden when `len(agents) == 1`.

- `quorum` is a single `<select name="human_gate[quorum]">` with three
  options matching the schema enum.
- Remote user rows follow `.agents/skills/key_value_form_pattern/SKILL.md`:
  - Container: `#remote-users-rows`
  - Row: `<fieldset class="remote-users__row form-group--nested" data-remote-index="N">`
  - **No** hidden `id` field is submitted — the server derives `id` from
    `name` on every save. Existing-row templates may render the current
    slug as a read-only `<code class="remote-users__id-badge">` next to the
    name for transparency, but it is purely informational.
  - Inputs: `name="human_gate[remote_users][N][name]"` and `[description]`.
  - Add button: `.js-add-remote-user` in the subsection header.
  - Per-row delete: `.chat-session-item__delete.js-delete-remote-user`.
- All textareas in this block must include a `<small class="form-hint">`
  (AGENTS.md rule #22).

JS row-handler responsibilities:

- `reindexRemoteUsers()` rewrites every `name="human_gate[remote_users][N]…"`
  attribute and every `id`/`for` association so the form continues to submit
  contiguous indices after a delete.
- `syncSingleAssistantMode()` toggles `#human-gate-remote-block.hidden` and
  disables every input inside it when the project has exactly one assistant.

---

## View parser (`server/views.py::_parse_remote_users`)

Iterate `human_gate[remote_users][N][name|description]` while any of
those two bracket fields is present in `post_data`. Skip rows where
`name` is blank (UI may submit a fresh empty row). The `[id]` field, if
submitted by older clients, is ignored — schema validation re-derives the
slug from `name`.

Pass the parsed list through under `human_gate.remote_users` and read
`human_gate[quorum]` for the enum value (default `"yes"`).

---

## Validation (`server/schemas.py::validate_human_gate`)

1. Coerce / migrate `quorum` (legacy bool → enum) and reject unknown values.
2. Iterate `remote_users`:
   - Skip blank `name` rows silently.
   - Reject duplicate names (case-insensitive).
   - Derive `id` from `name` using the agent-name slug rules
     (`re.sub(r"[\s\-]+", "_")` → `re.sub(r"[^\w]", "")` → must be a Python
     identifier). Reject names that produce an empty slug or a duplicate slug.
3. Apply reset rules when `enabled = false`.
4. In `validate_project()` (after `validate_human_gate`), reject when
   `assistant_count == 1 and human_gate["remote_users"]`.

### Renaming a remote user

Because `id` is derived from `name`, editing the `name` of an existing row
changes its `id`. Any per-session Redis state (tokens, presence, checked-set)
keyed by the **old** `id` becomes unreachable for the renamed user. This is
intentional — treat a rename as creating a new participant. The Phase 2
readiness lobby will simply present the new slug; the leader re-issues a
join URL.

---

## Readonly view (`config_readonly.html`)

When `human_gate.enabled` and `len(agents) > 1`:

- Render the `quorum` mode in plain English.
- List remote users (`name`, `description`).

Do not render an enabled/disabled badge — per-user enable/disable is a
runtime concern (lobby/readiness), not config state.

---

## Out of scope (Phase 1)

- Per-session join URL minting and `chat_sessions` membership (Phase 2).
- WebSocket / Channels delivery to the remote chat surface (Phase 3).
- Redis-backed remote response collection feeding `discussions[]` (Phase 3).
- Selector prompt enrichment with the remote-user roster (Phase 3).

When implementing those phases, this skill must be updated alongside the new
contract changes.

---

# Phase 2 — Readiness Lobby (pre-run gate)

Before any agent run starts, the leader sees an in-history "readiness lobby"
panel listing the configured remote users with a per-row checkbox, **Copy
Invitation Link** button (returns the existing token if one is active, or
mints a fresh one), and an **Online / Offline** status pill. The run only
proceeds once every *checked* user is online (or none are checked). Mirrors
the MCP OAuth gate UX exactly.

## Status enum addition (mandatory)

`chat_sessions.status` adds the value `"awaiting_remote_users"`. Every place
that lists allowed statuses must include it:

- `server/views.py::chat_session_run` `valid_states` tuple
- `server/services.py::try_set_session_running` `$in` array
- Any new transition helper

The pre-run gate runs **before** the MCP OAuth gate — the leader must resolve
human presence before triggering external authorizations.

## Session document fields

`chat_sessions` documents gain:

- `pending_remote_users`: `list[{user_id, name}]` — the configured remote
  users still required for this run. Cleared by `try_set_session_running()`.

`normalize_chat_session()` carries this list through to templates.

## Redis keys (namespace = `REDIS_NAMESPACE`)

| Key pattern | Value | TTL |
|---|---|---|
| `{ns}:remote_user_token:{session_id}:{token}` | `user_id` | `REMOTE_USER_TOKEN_TTL_SECONDS` (12 h / 43200 default) |
| `{ns}:remote_user_token_by_user:{session_id}:{user_id}` | `token` (active token for rotation) | matches token TTL |
| `{ns}:remote_user_online:{session_id}:{user_id}` | `"1"` | `REMOTE_USER_PRESENCE_TTL_SECONDS` (45 s default) |
| `{ns}:remote_user_checked:{session_id}` | JSON list of `user_id`s | `REMOTE_USER_CHECKED_TTL_SECONDS` (12 h / 43200 default) |

All four prefixes are purged on session delete via
`agents.session_coordination.purge_remote_users_state(session_id)`. **Never log
token strings or join URLs**.

## Helpers in `agents/session_coordination.py`

- `get_or_mint_remote_user_token(session_id, user_id) -> token` — idempotent default path used by readiness token endpoint
- `mint_remote_user_token(session_id, user_id) -> token` — atomically rotates
- `lookup_remote_user_token(session_id, token) -> user_id|None`
- `set_remote_user_online(session_id, user_id)` / `clear_remote_user_online(...)`
- `list_online_remote_users(session_id, user_ids) -> list[str]`
- `list_remote_users_with_token(session_id, user_ids) -> list[str]`
- `set_checked_remote_users(session_id, user_ids)` / `get_checked_remote_users(session_id) -> list[str]|None`
- `purge_remote_users_state(session_id)`

`get_checked_remote_users` returns `None` when the leader has not chosen yet;
the gate then defaults to "all configured users are required".

## Helpers in `server/services.py`

- `compute_pending_remote_users(project, session_id) -> list[{user_id, name}]`
- `set_session_awaiting_remote_users(session_id, pending_users)` (mirrors
  `set_session_awaiting_oauth`)
- `get_remote_users_status(project, session_id) -> {users: [{user_id, name,
  description, online, checked, has_token}]}`

## Invitation links — stable for the Redis TTL

- `POST /chat/sessions/<id>/readiness/<user_id>/token/` is **idempotent** and
  uses `agents.session_coordination.get_or_mint_remote_user_token()`. It
  returns the existing token if one is still alive in Redis; otherwise it
  mints a fresh one. Repeated Copy clicks therefore return the same URL
  during the configured TTL.
- `GET /readiness/status/` includes `join_url` for any user that already has
  a token, so the panel can render "Copy Invitation Link" without a
  round-trip; users with no token render "Generate Invitation Link" and the
  first click triggers `get_or_mint`.
- **Redis is short-lived run state, NOT long-term persistence**. Default TTLs
  are 12 h (`REMOTE_USER_TOKEN_TTL_SECONDS=43200`,
  `REMOTE_USER_CHECKED_TTL_SECONDS=43200`). A session resumed after the TTL
  expires simply mints a new token on the leader's next Copy click — there
  is no MongoDB or filesystem persistence of token material.
- Tokens, URLs, and any value derived from them are NEVER logged or attached
  to OTel spans (only `session_id`, `user_id`, `rotated`, `pending_count`).

## HTTP endpoints

| Method + path | Purpose |
|---|---|
| `GET  /chat/sessions/<id>/readiness/status/` | Snapshot consumed by the lobby panel (3 s polling). |
| `POST /chat/sessions/<id>/readiness/check/` | Form field `user_ids` (repeated). Persists checked-set in Redis. Validates IDs against project config. |
| `POST /chat/sessions/<id>/readiness/<user_id>/token/` | Mints (or rotates) a token. Returns `{join_url}`. |

All three are admin-secret gated (`X-App-Secret-Key`). Tokens are URL-safe
random strings (`secrets.token_urlsafe(32)`) — never derived from `user_id`.

## Pre-run gate in `chat_session_run`

```python
pending_remote = compute_pending_remote_users(project, session_id)
if pending_remote:
    set_session_awaiting_remote_users(session_id, pending_remote)
    return JsonResponse({"status": "awaiting_remote_users", "users": pending_remote}, status=409)
```

This block lives **before** the existing MCP OAuth check and **before** lease
acquisition. `chat_session_stop` flips `awaiting_remote_users` → `idle` so
the Cancel button on the lobby panel resets cleanly without an SSE stream.

## Frontend (`server/static/server/js/home.js`)

- `_showReadinessPanel(sessionId, secretKey, replayTask, replayAttachmentIds)`
  — appends `.chat-readiness-panel` to the chat history with rows
  `[checkbox][name][Copy/Generate Invitation Link btn][status pill]` plus a
  footer that pairs a left-aligned `__disclaimer` ("Participant configuration
  is locked for this chat. To add or remove remote participants, start a new
  chat session.") with a right-aligned Cancel button.
- 409 branch in `_doStartRun`: `awaiting_remote_users` is checked **before**
  `awaiting_oauth`.
- 3 s polling via `GET /readiness/status/`. Auto-replays `_doStartRun` once
  every checked user is online (or none are checked).
- Reload restoration: server renders
  `.chat-status-badge--remote-users[data-readiness-context]` in
  `chat_session_history.html`; both `DOMContentLoaded` and `htmx:afterSwap`
  handlers call `_restoreReadinessFromBadge()`.

## SCSS

`.chat-readiness-panel` mirrors `.chat-oauth-panel`: token-only colors,
`$color-success` for the Online pill, `$color-text-muted` for Offline,
`$color-primary` left border accent. Per rules #19 and #23.

## Observability

| Event name | Extras |
|---|---|
| `agents.session.awaiting_remote_users` | `session_id`, `pending_count` |
| `agents.remote_user.token_minted` | `session_id`, `user_id`, `rotated` |

OTel span: `agents.remote_user.token_mint`. **Never** log `token`, `join_url`,
or any value derived from them.

## Out of scope (Phase 2)

- Actual remote-user page (`GET /chat/<id>/remote_user/<token>/`) and WS
  consumer (Phase 3).
- Real-time presence updates: in Phase 2 a remote user only flips to Online
  when a Phase 3 WS heartbeat (or a manual `redis-cli SET …:remote_user_online:…`
  for testing) populates the presence key.
- Multi-participant gate response collection, quorum evaluation, and active
  responder selection (Phase 3).
