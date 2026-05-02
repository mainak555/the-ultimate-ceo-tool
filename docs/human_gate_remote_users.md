# Human Gate — Remote Users

Multi-user collaborative extension of the Human Gate. The local user (the
person running this app) is always the **session leader**; additional people
configured under *Project Configuration → Human Gate → Remote Users* can be
required to join an active chat session before each run starts.

This document is the canonical user / developer / agent reference. The
machine-enforced contract for code changes lives in
[`.agents/skills/human_gate_remote_users/SKILL.md`](../.agents/skills/human_gate_remote_users/SKILL.md);
this document is the human-readable counterpart.

> **Status**: Phase 1 (configuration) and Phase 2 (readiness lobby) are
> shipped. Phase 3 now includes a dedicated remote-user page for turn-gated
> replies, attachment upload, delegated export access, and AutoGen
> `UserProxyAgent` participant wiring for configured remote users.

---

## 1. Roles

| Role | Who | Capabilities |
|---|---|---|
| **Session leader** | The person who has `APP_SECRET_KEY` and runs the app | Owns MCP authorizations, starts every run, ticks the readiness lobby checkboxes, mints invitation links. |
| **Remote user** | Any participant configured under Human Gate → Remote Users | Joins an active chat session via a per-session invitation URL and responds at the Human Gate. Cannot start runs, cannot edit configuration, cannot authorize MCP servers. |

The leader / remote-user split is **architectural**, not a permission tier:
remote users never receive the admin secret. Their only credential is a
short-lived URL token issued by the leader.

---

## 2. Configuration (Phase 1)

### Where

*Project Configuration → Human Gate*. The Remote Users block is only
visible when:

- Human Gate is **enabled**, and
- the project has **two or more** assistant agents (selector routing is
  required to address replies — single-assistant mode rejects any non-empty
  `remote_users` list).

### Fields per row

| Field | Required | Notes |
|---|---|---|
| `name` | Yes | Displayed verbatim in the readiness lobby and (Phase 3) in chat. Must be unique within the list (case-insensitive). |
| `description` | Recommended | Plain-language role used by the Selector prompt to decide when to address a remote user. |

There is **no** per-user enable toggle and **no** stored `id` field.

- `id` is **server-derived** from `name` using the same slug rules as agent
  names (`[\s\-]+ → _`, non-word chars stripped, must be a Python
  identifier). Renaming a row changes the slug — treat it as a new
  participant; any active invitation URL for the old slug is orphaned.
- "Required for this run" is a **runtime** decision made in the readiness
  lobby (see §3), not a stored config field.

### Quorum

`quorum` controls how many remote replies are needed to continue past a
Human Gate pause (Phase 3 — Phase 2 only validates presence).

| Value | Meaning |
|---|---|
| `yes` *(default)* | Wait for **all** required remote users **and leader response**. |
| `first_win` | **Any one responder** unblocks the run (leader or any required remote). |
| `team_config` | Team-runtime targeting decides responders for the round: `round_robin` picks one deterministic remote user; `selector` parses the latest assistant hint line `REMOTE_USERS: user_a, user_b, leader` (`leader`/`gate` alias supported). When absent/invalid, falls back to all required remote users and no leader target. |

### Reset rules

- Disabling Human Gate clears `remote_users = []`, `quorum = "yes"`,
  `name = ""`.
- Reducing the team to a single assistant rejects the save with a clear
  validation error if `remote_users` is non-empty.

### Legacy migration

Historical documents may carry `quorum: bool`. Both reads and validation
silently migrate `True → "yes"`, `False → "first_win"`. Older UUID-based
`id` values in `remote_users[]` are re-derived to slugs on next save.

---

## 3. Readiness lobby (Phase 2)

### What the leader sees

When the leader clicks **Send** to start a run on a project that has any
configured remote users, the run does **not** start immediately. Instead a
panel is appended to the chat history:

```
┌ 👥 Waiting for remote participants ─────────────────────────┐
│ Check the participants required for this run, share the    │
│ invitation link with each one, and the run will start      │
│ automatically once everyone is online. You are the session │
│ leader and are always counted as online.                   │
│                                                            │
│  ☐  Alice Researcher       [Copy Invitation Link]  Offline │
│  ☑  Bob Designer           [Copy Invitation Link]  Online  │
│  ☐  Carol Reviewer         [Generate Invitation Link]  —   │
│                                                            │
│ 🔒 Participant configuration is locked for this chat. To   │
│    add or remove remote participants, start a new chat     │
│    session.                            [ Cancel run ]      │
└────────────────────────────────────────────────────────────┘
```

Behavior:

1. **Tick the checkbox** for every remote user whose presence is required
   for this run. Unchecked rows are not blocking, even if they are offline.
2. **Copy / Generate Invitation Link** mints (or returns the existing)
   per-session invitation URL. The button is **idempotent** — clicking it
   repeatedly during the same session returns the **same URL**, safe to
   share via Slack / email / SMS.
3. **Status pill** flips Offline → Online when the remote user opens the
  invitation URL in their browser; flips back to Offline within ~60 s of
   the browser tab closing or the network dropping.
4. The run **auto-starts** as soon as every checked row is online. No extra
   click is needed.
5. **Cancel run** abandons the lobby, returns the session to `idle`, and
   removes the panel.

### Reload safety

If the leader refreshes the browser while the lobby is active,
`chat_session_history.html` re-renders the panel from the persisted
`chat_sessions.status = "awaiting_remote_users"` field plus a fresh Redis
snapshot. No state is lost.

### Fallback to current Human Gate behavior

If `remote_users` is empty, or if the leader's checked set for this run is
empty, the run falls back to the existing leader-only Human Gate behavior.
This fallback applies uniformly to `yes`, `first_win`, and `team_config`.

---

## 3.1 AutoGen participant model

When Human Gate is enabled and `remote_users` is non-empty, team construction
adds one non-blocking `UserProxyAgent` participant per configured remote user.

- The session leader is intentionally **not** represented as a `UserProxyAgent`.
- Remote proxy participants are added for team roster/context awareness.
- Human input collection remains in the existing gate flow (WebSocket + Redis),
  not inside AutoGen inline input prompts.

The selector prompt includes a guardrail instructing the router not to pick
remote proxy participants during live runs; remote responses are collected via
the Human Gate panel and replayed on resume.

### Order vs. MCP OAuth

Both gates can fire on a single run. The order is:

```
POST /chat/sessions/<id>/run/
   1. Readiness gate          → HTTP 409 awaiting_remote_users
   2. MCP OAuth gate          → HTTP 409 awaiting_oauth
   3. Acquire Redis lease     → SSE stream begins
```

Resolving the readiness gate first is intentional — there is no point
prompting for OAuth credentials if the humans aren't even present.

---

## 4. URL & token lifecycle

### Invitation URL shape

```
{BASE_URL}/chat/{session_id}/remote-user/{token}/
```

- `token` is `secrets.token_urlsafe(32)` — never derived from `user_id`.
- The URL is **session-scoped**: the same person joining a different chat
  session needs a fresh URL.
- Only the leader can mint the URL (admin-secret gated endpoint).

### Stability

The token endpoint uses `get_or_mint_remote_user_token()`:

- First Copy click → mints a token, stores it in Redis with both forward
  and reverse keys, returns the URL.
- Subsequent Copy clicks within the TTL → return the **same** URL without a
  fresh mint.
- After the TTL expires both keys vanish; the next Copy click mints a new
  token and the previously-shared URL becomes a 404.

### Why Redis-only

Redis is treated as **short-lived run state**, never long-term persistence.
Token material is never written to MongoDB or to disk. A session resumed
days later can simply re-issue invitation links — there is no token-recovery
flow because there is nothing to recover. This keeps the security surface
small (no stale credentials to rotate or audit) and matches how the rest of
the run-coordination layer (lease, cancel signal, presence) already works.

### TTL summary

| Env var | Default | What it controls |
|---|---|---|
| `REMOTE_USER_TOKEN_TTL_SECONDS` | `43200` (12 h) | Lifetime of a minted invitation token (and its reverse-lookup key). |
| `REMOTE_USER_PRESENCE_TTL_SECONDS` | `60` | How long the remote user is considered online without a fresh heartbeat. |
| `REMOTE_USER_HEARTBEAT_INTERVAL_SECONDS` | `30` | How often the remote user's browser refreshes its presence key (Phase 3 cadence). Must satisfy `presence_ttl ≥ 2 × heartbeat_interval`. |
| `REMOTE_USER_CHECKED_TTL_SECONDS` | `43200` (12 h) | Lifetime of the leader's "required for this run" checkbox set. |

12 h covers a normal working day. If your operational pattern is different
(multi-day workshops, async global teams), tune `*_TOKEN_TTL_SECONDS` and
`*_CHECKED_TTL_SECONDS` together.

---

## 5. Status, badges, and chat session states

`chat_sessions.status` accepts `awaiting_remote_users` alongside the existing
`idle`, `running`, `awaiting_input`, `awaiting_oauth`, `error`, `completed`
states. Transitions:

```
idle ──Send─▶ awaiting_remote_users ──all checked online─▶ awaiting_oauth ─▶ running
                  │
                  └──Cancel run──▶ idle
```

When status is `awaiting_remote_users`, the chat history renders a
`.chat-status-badge--remote-users` pill alongside the panel so reload
recovery and second-tab views show the same gate state.

---

## 6. Endpoints (admin-secret gated)

| Method + path | Purpose |
|---|---|
| `POST /chat/sessions/<id>/run/` | Returns HTTP 409 `{status: "awaiting_remote_users", users: [...]}` when the readiness gate is unsatisfied. |
| `GET  /chat/sessions/<id>/readiness/status/` | Snapshot used by the lobby panel (3 s polling). Returns per-row `{user_id, name, description, online, checked, has_token, join_url}`. `join_url` is populated when a token is already alive. |
| `POST /chat/sessions/<id>/readiness/check/` | Form field `user_ids` repeated; persists the leader's checkbox set. |
| `POST /chat/sessions/<id>/readiness/<user_id>/token/` | **Idempotent** — returns existing or freshly-minted invitation URL. |
| `POST /chat/sessions/<id>/stop/` | Resets `awaiting_remote_users` → `idle` and tears down the lobby. |

All readiness endpoints above require `X-App-Secret-Key`. There is **no**
unauthenticated or cookie-based variant for the leader-side readiness lobby.

### Phase 3 remote-user endpoints

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /chat/<session_id>/remote-user/<token>/` | join URL token | Render remote-user page (chat history, turn-gated input, presence strip). |
| `POST /chat/sessions/<id>/remote/heartbeat/` | `X-Remote-User-Token` | Refresh online presence TTL. |
| `POST /chat/sessions/<id>/remote/attachments/` | `X-Remote-User-Token` | Upload remote participant attachments during gate turn. |
| `WS /ws/chat/<session_id>/remote-user/<token>/` | join URL token | Live remote channel: receives pushed `state` updates and accepts `submit_reply`, `heartbeat`, and `sync_state` client messages. |

### Remote export authorization

Remote pages never receive `APP_SECRET_KEY`. Export popup calls use delegated
header `X-Remote-Export-Capability` (session-scoped Redis token bound to
`{session_id, user_id}`). Session-scoped Trello/Jira routes accept either:

- `X-App-Secret-Key` (leader/admin path), or
- `X-Remote-Export-Capability` (remote path).

---

## 7. Security & redaction rules

These are mandatory; violations are caught by `.agents/skills/observability_logging/SKILL.md`.

- **Never log token strings, invitation URLs, or any value derived from
  them.** Allowed log/span attributes: `session_id`, `user_id`,
  `pending_count`, `rotated` (bool).
- The OAuth `?skey=` query param exception (popup window) does **not** apply
  here — readiness endpoints are header-only.
- Tokens are random (32-byte URL-safe), never derived from `user_id` or any
  user-controlled input.
- Treat the OTLP backend as PII-bearing for chat content; remote-user names
  and descriptions are forwarded as span attributes the same way agent
  names already are.
- Session delete must call `purge_remote_users_state(session_id)` to wipe
  all four Redis prefixes (token, token-by-user, presence, checked-set)
  before deleting MongoDB rows.

---

## 8. Storage map

| Layer | Stores | Lifetime |
|---|---|---|
| MongoDB `project_settings.human_gate.remote_users` | Configured roster (`name`, `description`, derived `id`) | Permanent (until the project is edited). |
| MongoDB `chat_sessions.status` + `pending_remote_users` | Current gate state for resume | Until session is deleted or run completes. |
| Redis `{ns}:remote_user_token:*` and `*_token_by_user:*` | Active invitation tokens (forward + reverse) | `REMOTE_USER_TOKEN_TTL_SECONDS` (12 h). |
| Redis `{ns}:remote_user_online:*` | Per-user presence flag | `REMOTE_USER_PRESENCE_TTL_SECONDS` (60 s). |
| Redis `{ns}:remote_user_checked:*` | Leader's checkbox set for this session | `REMOTE_USER_CHECKED_TTL_SECONDS` (12 h). |

No token, URL, or presence flag is ever written to MongoDB or to disk.

---

## 9. Developer reference (modules & helpers)

### Backend

| File | Responsibility |
|---|---|
| [server/schemas.py](../server/schemas.py) | `validate_human_gate()` — slug derivation, duplicate rejection, quorum enum, single-assistant guard. |
| [server/services.py](../server/services.py) | `normalize_project()` (read-side migration), `compute_pending_remote_users()`, `set_session_awaiting_remote_users()`, `get_remote_users_status(project, session_id, base_url)`. |
| [server/views.py](../server/views.py) | `_parse_remote_users()` form parser, `chat_session_run` 409 branch, `chat_session_readiness_status / _check / _token` endpoints. |
| [agents/session_coordination.py](../agents/session_coordination.py) | All Redis key construction + helpers: `mint_remote_user_token`, `get_or_mint_remote_user_token`, `lookup_remote_user_token`, `set/clear/list_remote_user_online`, `set/get_checked_remote_users`, `purge_remote_users_state`. |
| [config/settings.py](../config/settings.py) | The four `REMOTE_USER_*` env-var defaults. |

### Frontend

| File | Responsibility |
|---|---|
| [server/static/server/js/home.js](../server/static/server/js/home.js) | `_doStartRun` 409 branch, `_showReadinessPanel`, `_renderReadinessRows`, `_attachReadinessBehavior`, `_copyInvitationToClipboard`, `_restoreReadinessFromBadge`. |
| [server/templates/server/partials/chat_session_history.html](../server/templates/server/partials/chat_session_history.html) | `.chat-status-badge--remote-users[data-readiness-context]` for reload recovery. |
| [server/static/server/scss/main.scss](../server/static/server/scss/main.scss) | `.chat-readiness-panel` (token-only theming). Mirrors `.chat-oauth-panel`. |

### Shared with other features

The Remote Users editor reuses [`.agents/skills/key_value_form_pattern/SKILL.md`](../.agents/skills/key_value_form_pattern/SKILL.md)
(grid layout, `+ Add` button in subsection header, `chat-session-item__delete`
× per row, JS reindex on remove). Do not introduce a parallel pattern.

---

## 10. Observability events

| Event name | Level | Extras |
|---|---|---|
| `agents.session.awaiting_remote_users` | INFO | `session_id`, `pending_count` |
| `agents.remote_user.token_minted` | INFO | `session_id`, `user_id`, `rotated` |
| `agents.remote_user.presence_updated` | DEBUG | `session_id`, `user_id`, `online` |

OTel spans:

- `service.session.compute_pending_remote_users` — child of the
  `agents.session.run` root trace.
- `agents.remote_user.token_mint` — `service_name` `product-discovery`,
  attributes limited to `session_id`, `user_id`, `rotated`.

Token strings, join URLs, and Redis values are never set as span attributes.

---

## 11. Phase-3 runtime (shipped)

When a run pauses at Human Gate (`awaiting_input`), remote-user replies are
collected and consumed by the next resume flow:

1. WebSocket / Channels delivery powers the remote-user page at
  `/chat/<id>/remote-user/<token>/`.
2. Redis round-state stores one reply payload per remote user (`text` +
  `attachment_ids`) for `(session_id, gate_round)`.
3. `POST /chat/sessions/<id>/respond/` with `action=continue` first enforces
  quorum server-side. If responders are still pending, it returns HTTP 409
  `{status:"awaiting_remote_users", users:[...]}` and does not resume.
  The leader UI may also call `action=continue_auto` during gate polling when
  the compose box is empty and no compose attachments are queued; this allows
  automatic resume as soon as remote quorum is satisfied without typed leader
  text.
4. When quorum is satisfied, queued remote payloads are popped and merged into
  the next run task as a `Remote participant responses:` context block; queued
  remote attachment IDs are also merged into the resume attachment set.

Remote users are represented as non-blocking `UserProxyAgent` participants in
team build when Human Gate is enabled and `remote_users` is non-empty. The
session leader remains outside that participant list. Human input collection
continues through the gate flow (WebSocket + Redis), not inline blocking model
input prompts.

---

## 12. Quick checklist for new code that touches this feature

1. ☐ Did you read [`.agents/skills/human_gate_remote_users/SKILL.md`](../.agents/skills/human_gate_remote_users/SKILL.md) first?
2. ☐ All Redis access goes through `agents/session_coordination.py` helpers (no ad-hoc keys in `server/`).
3. ☐ No log line, span attribute, or template output contains a token, join URL, or any derived value.
4. ☐ Single-assistant projects still reject `remote_users` configuration.
5. ☐ New session-status values are added to `valid_states` AND `try_set_session_running()` `$in` AND `chat_session_history.html` reload-recovery scan.
6. ☐ Session delete still calls `purge_remote_users_state(session_id)`.
7. ☐ Datetime fields (if any added) follow [`.agents/skills/datetime_storage/SKILL.md`](../.agents/skills/datetime_storage/SKILL.md).
