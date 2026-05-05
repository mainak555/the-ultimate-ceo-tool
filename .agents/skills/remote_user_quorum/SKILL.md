# Skill: remote_user_quorum

## Purpose

Implementation reference for remote users and human-gate quorum in chat sessions.
Required reading before:

- Changing `chat_session_respond`, `event_stream`, or any quorum gate helper
- Integrating new quorum modes
- Extending `UserProxyAgent` beyond placeholder (`team_choice` live input)

---

## Current status and deferred scope

Implemented now:

- Remote user respond page/UI and public respond endpoint are live.
- Host and remote WebSocket subscriptions are live (`HostSessionConsumer`,
    `RemoteUserReadinessConsumer`, `RemoteChatConsumer`).
- `first_win` and `all` quorum modes use Redis gate responses, winner lock, and
    pending-task handoff for run replay.

Still deferred:

- `team_choice` live remote input to `UserProxyAgent.input_func` (current
    placeholder still returns immediate default input).

---

## Key invariants

1. **`quorum` and `remote_users` are not stored in `chat_sessions`** — always read both from `project["human_gate"]` at runtime. Team config changes (add/remove remote users, change quorum mode) take effect on the next run without creating a new session.
2. **`expected_names` ordering is fixed**: gate user first, then remote users in project config order. This order governs both discussion entry insertion and the composed task.
3. **Discussion entries are inserted atomically by the winner** — only after `claim_gate_winner()` returns True.
4. **`pop_pending_task()` is the handoff from respond-view to event_stream** — it is called once at run start and consumed atomically. A second call returns None.
5. **`all` mode requires explicit host final Continue** after all expected responder payloads are present; quorum completion is not auto-advanced by backend polling.
6. **Host WS continuity is required** — `home.js` must keep `/ws/session/<session_id>/` connected (including newly created sessions) so live remote bubbles and `first_win` `quorum_committed` resume signals are received.

---

## Session creation

### `server/views.py` — `chat_session_create`

The project is loaded for the 404 check only. No remote_users extraction or passing:

```python
project = services.get_project(project_id)
if project is None:
    return HttpResponse(..., status=404)
session = services.create_chat_session(project_id, description)
```

### `server/services.py` — `create_chat_session`

```python
def create_chat_session(project_id, description):
    doc = {
        ...
        # no remote_users field
    }
```

### `server/services.py` — `normalize_chat_session`

Does not return `remote_users` — callers read from project.

---

## Redis key schema

All keys use the namespace prefix `{NS}` = `f"{REDIS_NAMESPACE}:{env}"` where `REDIS_NAMESPACE`
defaults to `"chat_agent"` and `env` = `settings.ENVIRONMENT`.

**Gate keys** (cleared together by `clear_gate_responses` once quorum is met):

| Key | Value | TTL |
|-----|-------|-----|
| `{NS}:gate_response:{session_id}:{name}` | JSON `{"text": str, "attachment_ids": list}` | 6 h |
| `{NS}:gate_winner:{session_id}` | claimer name string | 6 h |
| `{NS}:pending_task:{session_id}` | JSON `{"task": str, "attachment_ids": list}` | 5 min |

**Remote user readiness keys** (cleared by `purge_remote_user_session_keys`):

| Key | Value | TTL |
|-----|-------|-----|
| `{NS}:remote_user:{session_id}:{user_name}:status` | `"online"` or `"ignored"` (absent = offline) | token TTL (24 h) |
| `{NS}:remote_user:{session_id}:{user_name}:token` | UUID invite token string | token TTL (24 h) |
| `{NS}:remote_user:token:{token}` | JSON `{"session_id", "user_name", "project_id"}` | token TTL (24 h) |
| `{NS}:quorum:{session_id}` | string `all \| first_win \| team_choice` | token TTL (24 h) |

Gate TTLs are safety nets only — gate keys are cleared explicitly on quorum completion.

---

## Redis helper contracts

All helpers live in `agents/session_coordination.py`. They use the single shared Redis client
`_get_client()`. Server-layer code must import them from there — never create a second Redis pool.

### Remote user status helpers

All three status setters follow the same pub/sub contract: write (or delete) the status key, then
publish `{"type": "update", "user_name": ..., "status": <new_status>}` to the session's readiness
channel.  `RemoteUserReadinessConsumer._listen_redis` reacts to this event by re-fetching all
statuses, recalculating `online_count`/`required_count`, and forwarding the enriched update to the
host WebSocket.

| Helper | Redis write | Published status |
|--------|-------------|------------------|
| `set_remote_user_online(session_id, user_name)` | `SET status_key "online"` | `"online"` |
| `set_remote_user_ignored(session_id, user_name)` | `SET status_key "ignored"` | `"ignored"` |
| `set_remote_user_offline(session_id, user_name)` | `DEL status_key` | `"offline"` |

`set_remote_user_offline` is called by `unignore_remote_user` in `server/remote_user_views.py`
(host re-checks the participant checkbox) **and** by the WebSocket disconnect handler when a
remote user's connection closes.  Both paths must publish so the host panel stays current.

### `store_gate_response(session_id, responder_name, text, attachment_ids)`

```python
key = _gate_response_key(session_id, responder_name)
_get_client().set(key, json.dumps({"text": text, "attachment_ids": attachment_ids or []}), ex=_GATE_RESPONSE_TTL)
```

Silent on Redis error (raises `SessionCoordinationError` which the view must handle).

### `get_gate_response(session_id, responder_name) → dict | None`

Returns `{"text": str, "attachment_ids": list}` or None when key is missing/expired.

### `check_all_gate_responses(session_id, expected_names) → (bool, dict)`

Pipelined `GET` for all `expected_names`. Returns `(all_present, collected)` where
`collected` maps each name that responded to its response dict. Preserves list order.

### `claim_gate_winner(session_id, claimer_name) → bool`

`SET NX` on the winner key. Returns True on first call; False on all subsequent calls.
**This is the concurrency mutex** — all quorum-complete paths must call this before
inserting discussion entries or storing the pending task.

### `clear_gate_responses(session_id, expected_names)`

`DEL` all per-user keys for `expected_names` + the winner key. Called by the winner
after writing discussion entries and pending task.

### `store_pending_task(session_id, task, attachment_ids=None)`

Stores the composed task with a 5-minute TTL. The short TTL ensures that a stale
pending task from a failed run does not accidentally seed the next run.

### `pop_pending_task(session_id) → dict | None`

Atomic `GETDEL` (Redis 6.2+) or pipeline `GET` + `DEL` fallback for older Redis.
Returns `{"task": str, "attachment_ids": list}` or None. Single-call contract —
subsequent calls for the same session always return None.

---

## `chat_session_respond` — quorum paths

### Pre-conditions (all quorum modes)

```python
project = services.get_project(session["project_id"])      # fresh project read
quorum = (project.get("human_gate") or {}).get("quorum") or "na"
remote_users = (project.get("human_gate") or {}).get("remote_users") or []  # from live project
gate_name = (project.get("human_gate") or {}).get("name") or "You"
expected_names = [gate_name] + [ru["name"] for ru in remote_users]
```

### `quorum == "na"` or `"team_choice"` (or `remote_users == []`)

Original single-step path: `set_session_status(idle)`, return `{status: "ok", task, attachment_ids}`.
Task is returned directly in the JSON response body for the frontend to use.

### `quorum == "first_win"`

```
claim_gate_winner(session_id, gate_name)  →  False → HTTP 409
store_gate_response(session_id, gate_name, text, attachment_ids)
bind_attachments_to_message(session_id, attachment_ids, msg_id)
append_messages(session_id, [discussion_entry_for_gate])
store_pending_task(session_id, text, attachment_ids)  # only if text or attachments
clear_gate_responses(session_id, expected_names)
set_session_status(session_id, "idle")
return {"status": "ok", "task": "", "attachment_ids": []}
```

Task is in Redis, not in the response body. Frontend triggers `/run/` which pops it.

### `quorum == "all"`

```
store_gate_response(session_id, gate_name, text, attachment_ids)

all_present, collected = check_all_gate_responses(session_id, expected_names)
if not all_present → HTTP 202 {"status": "waiting"}

publish_remote_user_event(..., {"type": "quorum_progress", ...})

claim_gate_winner(session_id, gate_name)  →  False → HTTP 202 {"status": "waiting"}

# Winner path:
entries = []
for name in expected_names:
    resp = collected[name]
    if resp["text"].strip() or resp["attachment_ids"]:
        entries.append(build_discussion_entry(
            agent_name=name,
            role="user",
            content=resp["text"],
            attachments=bind_and_enrich(resp["attachment_ids"]) if name == gate_name else [],
            timestamp=utc_now(),
        ))
append_messages(session_id, entries)

composed_task = "\n\n".join(
    resp["text"] for name in expected_names
    if (resp := collected[name]) and resp["text"].strip()
)
store_pending_task(session_id, composed_task, gate_attachment_ids)
clear_gate_responses(session_id, expected_names)
set_session_status(session_id, "idle")
publish_remote_user_event(..., {"type": "quorum_committed", ...})
return {"status": "ok", "task": "", "attachment_ids": [], "pending_task_ready": True}
```

Remote responders in `all` mode return HTTP 202 with:

- `{status: "waiting", all_present: false}` while quorum is still collecting.
- `{status: "waiting_host", all_present: true}` once all expected payloads are present and host final continue is required.

---

## `event_stream` — pending task consumption

At the top of the run body (before the human discussion-entry block):

```python
_pending = await asyncio.to_thread(_pop_pending_task, session_id)
if _pending is not None:
    task_for_agent = _pending["task"]
    pending_attachment_ids = _pending["attachment_ids"] or []
    # Do NOT insert another discussion entry — quorum path already did.
else:
    # Standard path: use `task` and `attachment_ids` from the request,
    # insert a human discussion entry as normal.
    if task or attachment_ids:
        ...
```

### Discussion entry suppression rule

When `pop_pending_task()` returns a value, the `if task or attachment_ids:` block that would
normally append a human discussion entry **must be skipped entirely**. The quorum respond view
already inserted ordered entries per responder.

### `UserInputRequestedEvent` SSE (team_choice)

```python
elif type(msg).__name__ == "UserInputRequestedEvent":
    yield _sse("remote_input_requested", {
        "proxy_name": getattr(msg, "source", ""),
        "request_id": str(getattr(msg, "request_id", "")),
    })
```

This event fires when the AutoGen runtime calls the `UserProxyAgent.input_func`.
Current implementation keeps this as an informational breadcrumb while team-choice
live remote input remains deferred.

---

## `build_team` — `team_choice` proxy wiring

`agents/team_builder.py`:

```python
def build_team(project: dict, session_id: str | None = None, remote_users: list | None = None):
    ...
    # After AssistantAgent loop:
    if (project.get("human_gate") or {}).get("quorum") == "team_choice" and remote_users:
        def _make_input_func(proxy_name):
            async def _placeholder(prompt):  # noqa: ARG001
                return "Continue."
            return _placeholder

        for ru in (remote_users or []):
            safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", ru["name"])
            agents.append(UserProxyAgent(
                name=safe_name,
                description=ru.get("description") or "Remote participant",
                input_func=_make_input_func(safe_name),
            ))
```

`n_agents = len(agents)` is computed **after** appending proxies, so `AgentMessageTermination`
automatically accounts for proxy turns.

---

## Deferred implementation hooks

### Team-choice live input (deferred)

Replace the placeholder func with an async func that:
1. Subscribes to a Redis pub/sub channel `{NS}:proxy_input:{session_id}:{proxy_name}`
2. Blocks until a message arrives (with timeout)
3. Returns the remote user's text payload

Publish the message from the remote user's HTTP POST endpoint.

---

## Quorum options — single source of truth

`QUORUM_OPTIONS` and `VALID_QUORUM_VALUES` are defined once in `server/util.py`:

```python
QUORUM_OPTIONS = [
    {"value": "all",         "label": "Wait for all remote users to reply"},
    {"value": "first_win",   "label": "First user response continues the run"},
    {"value": "team_choice", "label": "Let the agent planner decide who must reply"},
]
VALID_QUORUM_VALUES = {opt["value"] for opt in QUORUM_OPTIONS}
```

- `server/views.py` imports `QUORUM_OPTIONS` and passes it to templates as `quorum_options` (config form) and as `window._quorumOptions` JSON (home page).
- `server/remote_user_views.py` imports `VALID_QUORUM_VALUES` for `set_session_quorum_view` validation.
- Templates iterate `{% for opt in quorum_options %}` — never hardcode option values.

---

## Participants readiness panel — quorum dropdown contract

The `.chat-remote-panel` in `home.js` carries a quorum dropdown rendered by `_renderQuorumDropdown(panel, quorum)`.

### Visibility rule

`team_choice` is **only selectable via Project Config**. It must **not appear** in the live chat dropdown unless the project is already configured with that value:

```js
var visibleOptions = isTeamChoice
  ? allOptions          // show all three (disabled) so the value is visible
  : allOptions.filter(function (o) { return o.value !== "team_choice"; });
```

### Disabled rule

When `effectiveQuorum === "team_choice"` the `<select>` is rendered with `disabled`. Users cannot change quorum from `team_choice` in the chat panel.

### Idempotent update path

When the dropdown already exists (WS `state` refresh), the function:
1. Removes the `team_choice` option if `isTeamChoice` is false (handles reconnect after project config change).
2. Updates `value` and `disabled` in place.

### Page-load and session-switch reconnect

Both `htmx:afterSwap` and the `DOMContentLoaded` bootstrap call `_renderQuorumDropdown(panel, panel.dataset.quorum)` **before** `_attachRemoteUserGateBehavior`. This makes the dropdown appear immediately from the server-injected `data-quorum` attribute without waiting for the WS `state` message.

### `data-quorum` attribute — how it reaches the DOM

The server-rendered `.chat-remote-panel` in `chat_session_history.html` carries `data-quorum="{{ session_quorum|default:'na' }}"`.

`chat_session_detail` (and the create/swap OOB paths) resolve `session_quorum` for `awaiting_remote_users` sessions:

```python
session_quorum = None
if session.get("status") == "awaiting_remote_users":
    from agents.session_coordination import get_session_quorum
    session_quorum = get_session_quorum(session_id)
    if not session_quorum and project:
        session_quorum = (project.get("human_gate") or {}).get("quorum") or "na"
```

Resolution order: Redis quorum override → project config quorum → `"na"`.

---

## Files to touch when changing quorum behavior

| File | Why |
|------|-----|
| `agents/session_coordination.py` | Gate Redis helpers, key builders, TTL constants |
| `agents/team_builder.py` | `build_team()` UserProxyAgent wiring for team_choice |
| `agents/runtime.py` | `get_or_build_team()` remote_users passthrough |
| `server/views.py` | `chat_session_create`, `event_stream`, `chat_session_respond`, `chat_session_detail` |
| `server/util.py` | `QUORUM_OPTIONS`, `VALID_QUORUM_VALUES` (single source) |
| `server/remote_user_views.py` | `set_session_quorum_view` validation |
| `server/services.py` | `create_chat_session`, `normalize_chat_session` |
| `server/urls.py` | Route wiring for host and remote quorum endpoints |
| `server/consumers.py` | WebSocket quorum/reply event fanout for host and remotes |
| `server/static/server/js/home.js` | `_renderQuorumDropdown`, reconnect bootstrap paths |
| `server/templates/server/home.html` | `window._quorumOptions` JSON injection |
| `server/templates/server/partials/config_form.html` | Quorum `<select>` template loop |
| `server/templates/server/partials/chat_session_history.html` | `data-quorum` on `.chat-remote-panel` |
| `docs/agent_teams.md` | Update quorum/proxy docs |
| `docs/db_schema.md` | Update session schema |
| `.agents/skills/active_session_coordination/SKILL.md` | Update gate key contracts |
| `.agents/skills/remote_user_quorum/SKILL.md` | This file |

---

## AGENTS.md rules that apply

- Rule 58 — Active run coordination is Redis-backed and fail-fast
- Rule 69 — Single-assistant chat mode / remote users behavior
- Rule 70 — `AgentMessageTermination` for all team termination (proxies count in `n_agents`)
- Rule 78 — Mongo collection name constants (`CHAT_SESSIONS_COLLECTION`)
- Rule 57 — Datetime storage standard (`utc_now()`, BSON Date)
