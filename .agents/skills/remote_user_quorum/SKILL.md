# Skill: remote_user_quorum

## Purpose

Implementation reference for remote users and human-gate quorum in chat sessions.
Required reading before:

- Adding WebSocket / real-time remote user respond endpoints (Phase 2)
- Changing `chat_session_respond`, `event_stream`, or any quorum gate helper
- Integrating new quorum modes
- Extending `UserProxyAgent` beyond placeholder (Phase 2 `team_choice` live input)

---

## Phase 1 vs Phase 2 boundary

| Feature | Phase 1 (implemented) | Phase 2 (deferred) |
|---------|-----------------------|--------------------|
| `remote_users` session snapshot | ✅ | — |
| Redis per-user gate keys | ✅ | — |
| `first_win` quorum | ✅ | — |
| `all` quorum | ✅ (auto-complete remotes) | Real remote POST endpoint + removal of auto-complete |
| `team_choice` proxy wiring | ✅ (placeholder input func) | Real Redis pub/sub delivery to `UserProxyAgent.input_func` |
| `UserInputRequestedEvent` SSE breadcrumb | ✅ | WebSocket delivery |
| Remote user respond UI | ❌ | WebSocket chat panel / link |
| Remote user respond HTTP endpoint | ❌ | New route + view |
| Redis pub/sub on gate response | ❌ | Publish on `store_gate_response` |
| `consumers.py` WebSocket subscription | ❌ | Subscribe to readiness channel |

---

## Key invariants

1. **`quorum` is not stored in `chat_sessions`** — always read from `project["human_gate"]["quorum"]` at runtime.
2. **`remote_users` is stored in `chat_sessions`** — session snapshot written at creation time. Used as the composition lock for all team rebuilds. Never re-read from the live project.
3. **`expected_names` ordering is fixed**: gate user first, then remote users in project config order. This order governs both discussion entry insertion and the composed task.
4. **Discussion entries are inserted atomically by the winner** — only after `claim_gate_winner()` returns True.
5. **`pop_pending_task()` is the handoff from respond-view to event_stream** — it is called once at run start and consumed atomically. A second call returns None.
6. **Phase 1 auto-complete must be removed in Phase 2** — the `all`-quorum auto-complete block in `chat_session_respond` is a temporary stand-in for real remote user responses.

---

## Session creation

### `server/views.py` — `chat_session_create`

Before calling `services.create_chat_session`, load the project and extract the remote users:

```python
project = services.get_project(project_id)
remote_users = (project.get("human_gate") or {}).get("remote_users") or []
session = services.create_chat_session(project_id, description, remote_users=remote_users)
```

### `server/services.py` — `create_chat_session`

```python
def create_chat_session(project_id, description, remote_users=None):
    doc = {
        ...
        "remote_users": list(remote_users) if remote_users else [],
    }
```

### `server/services.py` — `normalize_chat_session`

```python
"remote_users": doc.get("remote_users") or [],
```

---

## Redis key schema

All keys use the namespace prefix `{NS}` = `f"{REDIS_NAMESPACE}:{env}"` where `REDIS_NAMESPACE`
defaults to `"chat_agent"` and `env` = `settings.ENVIRONMENT`.

| Key | Value | TTL |
|-----|-------|-----|
| `{NS}:gate_response:{session_id}:{name}` | JSON `{"text": str, "attachment_ids": list}` | 6 h |
| `{NS}:gate_winner:{session_id}` | claimer name string | 6 h |
| `{NS}:pending_task:{session_id}` | JSON `{"task": str, "attachment_ids": list}` | 5 min |

All three key groups are cleared together by `clear_gate_responses(session_id, expected_names)`
immediately after quorum is met and the session is set to `idle`. The TTLs are safety nets only.

---

## Redis helper contracts

All helpers live in `agents/session_coordination.py`. They use the single shared Redis client
`_get_client()`. Server-layer code must import them from there — never create a second Redis pool.

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
remote_users = session.get("remote_users") or []           # session snapshot
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

# Phase 1 ONLY — remove in Phase 2:
for ru in remote_users:
    store_gate_response(session_id, ru["name"], "", [])

all_present, collected = check_all_gate_responses(session_id, expected_names)
if not all_present → HTTP 202 {"status": "waiting"}

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
return {"status": "ok", "task": "", "attachment_ids": []}
```

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

This event fires when the AutoGen runtime calls the `UserProxyAgent.input_func`. In Phase 1
the func returns `"Continue."` immediately so the SSE is informational only. In Phase 2 this
event triggers a WebSocket push to the remote user's browser.

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

## Phase 2 implementation hooks

When implementing real remote-user responses (WebSocket phase):

### New files

- `server/remote_user_views.py` — `remote_user_respond(request, session_id)` endpoint
- Add route in `server/urls.py`: `POST /chat/sessions/<session_id>/remote-respond/`

### Changes to `agents/session_coordination.py`

After `store_gate_response(...)` in the new remote respond endpoint:

```python
# Publish readiness event so WebSocket consumer can notify all participants
redis.publish(
    f"{_namespace()}:gate_readiness:{session_id}",
    json.dumps({"responder": responder_name}),
)
```

### Changes to `server/consumers.py`

Subscribe to `gate_readiness:{session_id}` channel. On message: run
`check_all_gate_responses` and push a `gate_progress` WebSocket message
to all session participants.

### Changes to `team_choice` input func

Replace the placeholder func with an async func that:
1. Subscribes to a Redis pub/sub channel `{NS}:proxy_input:{session_id}:{proxy_name}`
2. Blocks until a message arrives (with timeout)
3. Returns the remote user's text payload

Publish the message from the remote user's HTTP POST endpoint.

### Remove Phase 1 auto-complete

In `chat_session_respond`, `quorum == "all"` path: delete the block:

```python
# Phase 1 ONLY — remove in Phase 2:
for ru in remote_users:
    store_gate_response(session_id, ru["name"], "", [])
```

Replace with: only proceed to the `check_all_gate_responses` check;
return HTTP 202 for gate user until all remotes have called the new remote-respond endpoint.

---

## Files to touch when changing quorum behavior

| File | Why |
|------|-----|
| `agents/session_coordination.py` | Gate Redis helpers, key builders, TTL constants |
| `agents/team_builder.py` | `build_team()` UserProxyAgent wiring for team_choice |
| `agents/runtime.py` | `get_or_build_team()` remote_users passthrough |
| `server/views.py` | `chat_session_create`, `event_stream`, `chat_session_respond` |
| `server/services.py` | `create_chat_session`, `normalize_chat_session` |
| `server/urls.py` | Phase 2: new remote-respond route |
| `server/consumers.py` | Phase 2: WebSocket gate readiness subscription |
| `docs/agent_teams.md` | Update quorum/proxy docs |
| `docs/db_schema.md` | Update session schema |
| `.agents/skills/active_session_coordination/SKILL.md` | Update gate key contracts |
| `.agents/skills/remote_user_quorum/SKILL.md` | This file |

---

## AGENTS.md rules that apply

- Rule 58 — Active run coordination is Redis-backed and fail-fast
- Rule 68 — Single-assistant chat mode / remote users behavior
- Rule 69 — `AgentMessageTermination` for all team termination (proxies count in `n_agents`)
- Rule 77 — Mongo collection name constants (`CHAT_SESSIONS_COLLECTION`)
- Rule 57 — Datetime storage standard (`utc_now()`, BSON Date)
