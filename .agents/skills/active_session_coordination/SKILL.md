---
name: active-session-coordination
description: Use when adding, changing, or reviewing chat run lifecycle, cancellation, or session runtime ownership. Enforces Redis-backed active lease + heartbeat + cancel signaling, fail-fast run start on Redis unavailability, and MongoDB durable resume state.
---

# Active Session Coordination Skill

Use this skill when touching:

- `server/views.py` run/start/stop/restart chat session handlers
- `agents/runtime.py` run lifecycle and cancellation
- `server/services.py` session status transitions used by run orchestration
- deployment/env docs related to runtime session coordination

## Mandatory contracts

### Coordination model

- Redis is the active coordination backend for running sessions.
- One active run lease per `session_id` at any time.
- Lease must be heartbeat-renewed while streaming.
- Cross-instance stop uses a Redis cancel signal keyed by `session_id`.
- Redis keys are ephemeral; never persist full team objects or MCP workbenches in Redis.

### Durability model

- MongoDB remains durable source of truth for:
  - `chat_sessions.discussions`
  - `chat_sessions.agent_state`
- Resume behavior must load from MongoDB checkpoints.

### Failure model

- Run start is **fail-fast** when Redis is unavailable.
- No in-memory fallback for distributed run ownership.
- If lease ownership is lost mid-run, cancel and stop session safely.

### Concurrency model

- Use atomic/conditional status transition to `running` (`idle|awaiting_input -> running`).
- Lease acquisition and Mongo status transition must both succeed before run starts.
- Lease must be released in all terminal paths (completed, stopped, error, disconnect).

### Single-assistant chat mode

- When a project has exactly one assistant agent, Human Gate is mandatory.
- In this mode, the run pauses after every assistant turn and returns `awaiting_input`.
- Do not use `team.max_iterations` as a completion condition for this mode.
- Conversation termination is human-controlled (`respond action=stop`) or cancellation/error.
- Keep the same lease/heartbeat/release guarantees as multi-agent flows.
- **Empty Continue is invalid** in single-assistant mode: no text and no attachments means
  the backend must return HTTP 400 **before** acquiring the Redis lease. The frontend must
  keep the Continue button disabled until the user types text or attaches a file.

### Graceful stop contract (human-gated runs)

When `cancel_team(session_id)` is called (e.g. Stop button):

1. Call `ExternalTermination.set()` first — fires at the next turn boundary so the current
   agent message is fully written before `TaskResult` is yielded.
2. Call `CancellationToken.cancel()` as a hard fallback — interrupts any in-flight LLM call.
3. `evict_team(session_id)` must clear `_TEAM_CACHE`, `_CANCEL_TOKENS`, and
   `_EXTERNAL_TERMINATIONS` so no stale signal leaks to a future session rebuild.

`ExternalTermination` is stashed in `project["_runtime"]["external_termination"]` by
`build_team()` and registered in `runtime._EXTERNAL_TERMINATIONS[session_id]` during
`get_or_build_team()` cache-miss. It is absent for non-gated runs; all code must
guard with `if ext_stop is not None`.

AutoGen automatically calls `termination.reset()` when the termination condition fires,
so `ExternalTermination._setted` and `AgentMessageTermination._count` reset cleanly between
gate rounds without any manual reset in `reset_cancel_token()`.

### Quorum gate coordination

Human gate quorum (`all` / `first_win`) is coordinated entirely through Redis. MongoDB is
only written once quorum is met — individual discussion entries per responder are appended
to `discussions[]` in defined order, then the session is set to `idle`.

#### Redis key schema (gate path only)

| Key | Pattern | TTL | Purpose |
|-----|---------|-----|---------|
| Per-user response | `{NS}:gate_response:{session_id}:{responder_name}` | 6 h | JSON `{text, attachment_ids}` per responder |
| Winner claim | `{NS}:gate_winner:{session_id}` | 6 h | SET NX — only one POST wins the race |
| Pending task | `{NS}:pending_task:{session_id}` | 5 min | Quorum-composed task string; consumed atomically by next `/run/` |

These keys are **always** cleared by `clear_gate_responses()` immediately after the winner
POSTs transitions the session to `idle`. The 24 h TTL is a safety net for abnormal termination.

#### Gate helpers — `agents/session_coordination.py`

| Function | Description |
|----------|-------------|
| `store_gate_response(session_id, responder_name, text, attachment_ids)` | `SET` individual key. Each user has their own key — no shared hash, no write contention |
| `get_gate_response(session_id, responder_name)` | `GET` + JSON decode. Returns dict or None |
| `check_all_gate_responses(session_id, expected_names)` | Pipelined `GET` for all names. Returns `(all_present: bool, collected: dict)` in list order |
| `claim_gate_winner(session_id, claimer_name)` | `SET NX` on winner key. True = won, False = already claimed |
| `clear_gate_responses(session_id, expected_names)` | `DEL` all per-user keys + winner key |
| `store_pending_task(session_id, task, attachment_ids)` | Stores composed task for next `/run/` |
| `pop_pending_task(session_id)` | `GETDEL` (Redis 6.2+) or pipeline GET+DEL. Returns `{task, attachment_ids}` or None |

#### `chat_session_respond` — quorum-aware `continue` path

`quorum` is read from the **project** (never from the session). `remote_users` is read from
the **session snapshot** (`session["remote_users"]`).

```python
expected_names = [gate_name] + [ru["name"] for ru in session["remote_users"]]
```

**`quorum == "na"` or `"team_choice"`** (no remote users or proxied via UserProxyAgent):
- Unchanged behavior. Set idle, return `{status: "ok", task, attachment_ids}`.

**`quorum == "first_win"`**:
1. `claim_gate_winner(session_id, gate_name)` — if False → HTTP 409.
2. `store_gate_response(session_id, gate_name, text, attachment_ids)`.
3. `attachment_service.bind_attachments_to_message(...)` + `services.append_messages(...)` with one discussion entry for the gate user.
4. `store_pending_task(session_id, text, attachment_ids)` (if text or attachment_ids).
5. `clear_gate_responses(session_id, expected_names)`.
6. `services.set_session_status(session_id, "idle")`.
7. Return `{status: "ok", task: "", attachment_ids: []}` — task already in Redis.

**`quorum == "all"`**:
1. `store_gate_response(session_id, gate_name, text, attachment_ids)`.
2. Phase 1 auto-complete: `store_gate_response(session_id, ru["name"], "", [])` for each remote user (they have no respond UI yet).
3. `check_all_gate_responses(session_id, expected_names)` — if not `all_present` → HTTP 202.
4. `claim_gate_winner(session_id, gate_name)` — if False → HTTP 202 (concurrent race already processing).
5. Winner: insert ordered discussion entries for each responder with non-empty text. Gate user first, then remote users in project config order. Use `attachment_service.bind_attachments_to_message` for gate user only.
6. Compose task: `"\n\n".join(text for text in collected_texts if text.strip())`.
7. `store_pending_task(session_id, composed_task, attachment_ids)`.
8. `clear_gate_responses(session_id, expected_names)`.
9. `services.set_session_status(session_id, "idle")`.
10. Return `{status: "ok", task: "", attachment_ids: []}`.

#### `event_stream` — pending task pop

At the start of the agent-run body (before the human discussion entry block):

```python
from agents.session_coordination import pop_pending_task as _pop_pending_task
_pending = await asyncio.to_thread(_pop_pending_task, session_id)
```

- If `_pending is not None`: the quorum path already inserted discussion entries.
  Use `_pending["task"]` and `_pending["attachment_ids"]` to build `task_for_agent`.
  Do **not** insert another human discussion entry (no `pending_messages.append`).
- If `_pending is None`: standard path — insert human discussion entry as before.

#### `UserInputRequestedEvent` in SSE loop

```python
elif type(msg).__name__ == "UserInputRequestedEvent":
    yield _sse("remote_input_requested", {
        "proxy_name": getattr(msg, "source", ""),
        "request_id": str(getattr(msg, "request_id", "")),
    })
```

This is emitted for `team_choice` proxy turns. In Phase 1 the proxy auto-completes immediately
so the run is never blocked. The SSE event is a breadcrumb for future WebSocket integration.

### Observability

- Use module logger (`logging.getLogger(__name__)`).
- Event names should follow `agents.session.*` dotted snake_case.
- Never log secrets/credentials from Redis URI or headers.
- Add tracing decorators for coordination operations.

## Anti-patterns

- Silent fallback to in-process-only coordination when Redis is down.
- Holding a lease across `awaiting_input` gate pauses.
- Storing `AssistantAgent`, team objects, or MCP workbenches in Redis.
- Releasing leases without ownership checks.
- Updating session status to `running` with an unconditional update.
- Reading `quorum` from `session` doc — it is not stored there; always read from `project["human_gate"]["quorum"]`.
- Reading `remote_users` from the live project inside a running session — use `session["remote_users"]` (snapshot). Project config may have changed since session create.
- Using `HSET` on a shared gate response hash — per-user `SET` on distinct keys avoids write contention.
- Inserting discussion entries into `discussions[]` before `claim_gate_winner()` returns True — the winner claim is the concurrency mutex.
- Inserting a human discussion entry in `event_stream` when `pop_pending_task()` returns a value — quorum path already inserted ordered entries.
- Calling `store_pending_task()` with the raw user task in `event_stream` when a quorum path is active — the respond view owns that step; `event_stream` only pops the task.
