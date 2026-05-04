# Chat Session Lifecycle — Architectural Analysis

## Overview Diagram

```mermaid
flowchart TD
    subgraph CLIENT["Client (browser / HTMX)"]
        A1["POST /chat/sessions/ — create session"]
        A2["POST /chat/sessions/<id>/run/ — start or resume"]
        A3["POST /chat/sessions/<id>/respond/ — gate decision"]
        A4["POST /chat/sessions/<id>/restart/ — restart from state"]
    end

    subgraph VIEWS["server/views.py"]
        B1["chat_session_create()"]
        B2["chat_session_run() — async SSE"]
        B3["chat_session_respond()"]
        B4["chat_session_restart()"]
    end

    subgraph GUARDS["Pre-run guards in chat_session_run"]
        C1["Secret key check"]
        C2["Session status in idle|awaiting_input|awaiting_mcp_oauth"]
        C3["Single-assistant mode: require task on non-first resume"]
        C4["MCP OAuth gate: compute_pending_oauth_servers → park awaiting_mcp_oauth"]
        C5["Redis availability: ensure_redis_available → 503 on fail"]
        C6["Lease acquisition: acquire_run_lease → 409 if already leased"]
        C7["try_set_session_running — atomic status move"]
    end

    subgraph RUNTIME["agents/runtime.py — in-process team cache"]
        D1["get_or_build_team(session_id, project, remote_users)"]
        D2["build_team() via team_builder.py"]
        D3["load_team_state() — restore from MongoDB on cache miss"]
        D4["reset_cancel_token()"]
        D5["save_team_state() → checkpoint_state()"]
        D6["cancel_team() — ExternalTermination.set() + CancellationToken.cancel()"]
        D7["evict_team() — purge cache + close MCP workbenches"]
    end

    subgraph TEAM_BUILD["agents/team_builder.py — build_team()"]
        E1["build_agent_runtime_spec() × N agents\n(system_prompt + model_client + MCP workbenches)"]
        E2["UserProxyAgent placeholders\n(team_choice quorum only)"]
        E3{"human_gate.enabled?"}
        E4["AgentMessageTermination(n_agents) | ExternalTermination()\nstash ext_stop → project._runtime.external_termination"]
        E5["AgentMessageTermination(n_agents × max_iterations)"]
        E6{"team.type?"}
        E7["RoundRobinGroupChat"]
        E8["SelectorGroupChat\n(≥2 agents required)\n+ selector_model + selector_prompt"]
    end

    subgraph SSE["event_stream() — inside chat_session_run"]
        F1["Heartbeat coroutine: renew_run_lease every N sec"]
        F2["pop_pending_task() — quorum-composed task from Redis"]
        F3["bind_attachments / build_attachment_context_block"]
        F4["_build_agent_task_for_run() → TextMessage or MultiModalMessage"]
        F5["team.run_stream(task, cancel_token)"]
        F6["TextMessage / ToolCallSummaryMessage → SSE 'message'"]
        F7["UserInputRequestedEvent → SSE 'remote_input_requested'"]
        F8["TaskResult → gate or done"]
        F9{"has_gate AND round < max_iter?"}
        F10["set status awaiting_input\nSSE 'gate'"]
        F11["set status completed\nevict_team\nSSE 'done'"]
        F12["CancelledError → status stopped + SSE 'stopped'"]
        F13["Exception → status idle + SSE 'error'"]
    end

    subgraph COORD["agents/session_coordination.py — Redis"]
        G1["Lease key: {NS}:chat_session:{id}:active_lease"]
        G2["Cancel key: {NS}:chat_session:{id}:cancel"]
        G3["Traceparent key: {NS}:chat_session:{id}:run_trace"]
        G4["Gate response keys: {NS}:gate_response:{id}:{name}"]
        G5["Gate winner key: {NS}:gate_winner:{id}"]
        G6["Pending task key: {NS}:pending_task:{id}"]
        G7["MCP OAuth keys (token + state + readiness)"]
    end

    subgraph RESPOND["chat_session_respond() — quorum routing"]
        H1{"quorum?"}
        H2["na / team_choice or no remotes\n→ set idle, respond immediately"]
        H3["first_win\n→ claim_gate_winner (NX)\n→ store_gate_response\n→ persist discussion entry\n→ store_pending_task\n→ set idle"]
        H4["all\n→ store_gate_response for gate user\n→ auto-complete remotes (Phase 1)\n→ check_all_gate_responses (pipeline GET)\n→ claim_gate_winner\n→ insert ordered discussion entries\n→ compose task\n→ store_pending_task\n→ set idle"]
    end

    A1 --> B1 --> services.create_chat_session
    A2 --> B2 --> C1 --> C2 --> C3 --> C4 --> C5 --> C6 --> C7
    C7 --> D1 --> D2 --> E1
    E1 --> E2
    E2 --> E3
    E3 -- yes --> E4
    E3 -- no --> E5
    E4 --> E6
    E5 --> E6
    E6 -- round_robin --> E7
    E6 -- selector --> E8
    D1 -- cache miss --> D3
    D1 --> D4 --> F1
    F1 & F2 & F3 --> F4 --> F5
    F5 --> F6 & F7 & F8
    F8 --> F9
    F9 -- yes --> F10
    F9 -- no --> F11
    F5 --> F12 & F13
    F10 --> A3 --> B3 --> H1
    H1 --> H2 & H3 & H4
    H2 & H3 & H4 --> A2
    A4 --> B4 --> D3
```

---

## 1. Session Initialization — `chat_session_create()`

`POST /chat/sessions/` → `server/views.py:645`

- Secret-key gated only.
- Calls `services.create_chat_session(project_id, description)` → MongoDB document with `status: "idle"`, empty `discussions[]`, `current_round: 0`.
- Returns HTMX OOB swaps: sidebar list + history panel + hidden `#active-session-id` input.
- **No team is built here** — team construction is deferred to the first `/run/`.

---

## 2. Run / Resume — `chat_session_run()`

`POST /chat/sessions/<id>/run/` — async, returns `text/event-stream`.  
Source: `server/views.py:865`

### Pre-run guard chain (sequential, fail-fast)

| Guard | Failure code |
|---|---|
| Secret key | 403 |
| Status not in `{idle, awaiting_input, awaiting_mcp_oauth}` | 409 |
| Single-assistant mode + non-first resume + empty task+attachments | 400 |
| Pending MCP OAuth servers → park `awaiting_mcp_oauth` | 409 |
| `ensure_redis_available()` — Redis ping | 503 |
| `acquire_run_lease()` — Redis SET NX | 409 |
| `try_set_session_running()` — atomic status move | 409 |

### Inside `event_stream()` (the SSE generator)

1. **Team resolution** — `get_or_build_team(session_id, project, remote_users)` (`agents/runtime.py`):
   - **Cache hit** → reuses existing in-process `RoundRobin/SelectorGroupChat` (keeps AutoGen internal history).
   - **Cache miss** → calls `build_team()`, then `load_team_state()` from MongoDB `agent_state`.

2. **Heartbeat** — a background coroutine calls `renew_run_lease()` every `REDIS_RUN_HEARTBEAT_SECONDS` (default 20 s). If the Lua compare-and-set fails (ownership lost), it cancels the token immediately.

3. **Task assembly**:
   - Checks Redis `pop_pending_task()` first (quorum-composed task deposited by `/respond/`).
   - Otherwise reads `task` + `attachment_ids` from POST body.
   - Builds `MultiModalMessage` for vision images; plain string otherwise.
   - Raw user-typed text (not attachment context) persists to `discussions[]` (rule 71).

4. **`team.run_stream()` loop** emits:
   - `TextMessage / ToolCallSummaryMessage` → SSE `"message"` → append to `pending_messages`.
   - `UserInputRequestedEvent` → SSE `"remote_input_requested"` (breadcrumb for `team_choice` quorum).
   - `TaskResult` → commits messages + checkpoints state → decides **gate vs done**.

5. **Termination decision** at `TaskResult`:
   - `has_gate AND (single_assistant_mode OR round < max_iter)` → `status = awaiting_input`, SSE `"gate"`.
   - Otherwise → `status = completed`, `evict_team()`, SSE `"done"`.

6. **Error paths**:
   - `CancelledError` → flush pending, checkpoint, `status = stopped`, SSE `"stopped"`.
   - Other exceptions → flush, checkpoint, `status = idle`, SSE `"error"` (user-friendly via `_friendly_run_error()`).
   - `finally` always: stop heartbeat, `release_run_lease()`, `clear_cancel_signal()`, end OTel span.

---

## 3. Team Building — `build_team()`

Source: `agents/team_builder.py`

```
project.agents → build_agent_runtime_spec() × N
  ├── system_prompt resolved (+ objective injected)
  ├── model_client built from agent_models.json
  └── MCP workbenches built (scope: none | shared | dedicated)

+ UserProxyAgent placeholders (team_choice quorum only)
  └── placeholder input_func → auto-returns "Continue."

termination:
  human_gate ON  → AgentMessageTermination(n_agents) | ExternalTermination()
  human_gate OFF → AgentMessageTermination(n_agents × max_iterations)

team_type:
  "round_robin" → RoundRobinGroupChat(agents, termination)
  "selector"    → SelectorGroupChat(agents, selector_model, selector_prompt, termination)
                  (requires ≥ 2 agents; invalid for single-assistant)
```

Key invariant: `AgentMessageTermination` counts only messages where `source != "user"` to avoid the off-by-one from AutoGen's built-in `MaxMessageTermination` (rule 70).

---

## 4. Quorum & Remote Users

Config lives in `project.human_gate` (validated in `server/schemas.py:331`):

```python
{
  "enabled": True,
  "name": "You",          # gate user (sanitized identifier)
  "quorum": "na|all|first_win|team_choice",
  "remote_users": [{"name": ..., "description": ...}]
}
```

`quorum` is forced to `"na"` if no `remote_users` are configured.

### Quorum routing in `chat_session_respond()`

Source: `server/views.py:1440`

| `quorum` | Behavior |
|---|---|
| `na` | No quorum logic — gate user responds, session immediately set to `idle`. |
| `team_choice` | No quorum logic (same as `na`) — `UserProxyAgent` placeholders handle the in-run flow via their auto-returning `input_func`. |
| `first_win` | `claim_gate_winner()` (Redis SET NX) — first POST wins. Stores response + persists discussion entry + deposits `store_pending_task()` → set `idle`. Concurrent 409. |
| `all` | All expected names must respond. Phase 1: gate user posts → remotes auto-completed with empty entries → `check_all_gate_responses()` (pipeline GET) → `claim_gate_winner()` → compose joint task → `store_pending_task()` → set `idle`. |

### Redis keys for quorum

Source: `agents/session_coordination.py`

| Key pattern | Purpose |
|---|---|
| `{NS}:gate_response:{id}:{name}` | Per-responder input; TTL-expiring |
| `{NS}:gate_winner:{id}` | SET NX race-winner; prevents double-resume |
| `{NS}:pending_task:{id}` | Quorum-composed task; consumed atomically via GETDEL in `pop_pending_task()` |

---

## 5. Stop / Cancel

- **Graceful stop** (`chat_session_respond(action="stop")` or Stop button): `evict_team()` + `status = stopped`.
- **Cross-instance cancel** (`POST /chat/sessions/<id>/stop/`): `signal_cancel()` → Redis cancel key → SSE loop calls `is_cancel_signaled()` every message → triggers `cancel_token.cancel()`.
- **In-process cancel** (`cancel_team()`): `ExternalTermination.set()` (graceful finish of current agent turn) → `CancellationToken.cancel()` (hard interrupt fallback).

---

## 6. State Persistence & Resume

- After every `TaskResult`: `save_team_state()` → serialized AutoGen state → MongoDB `chat_sessions.agent_state`.
- `MAX_AGENT_STATE_BYTES` (default 1 MB) guards document size — overflow is non-fatal (run completes; resume is unavailable).
- On cache miss (server restart / new instance): `load_team_state()` restores the full AutoGen conversation history so agents resume with context.
- Teams are **process-local** — horizontal scaling requires sticky sessions at the load balancer.

---

## Key Redis Key Reference

| Key pattern | Purpose | TTL source |
|---|---|---|
| `{NS}:chat_session:{id}:active_lease` | Active run ownership (Lua compare-and-set) | `REDIS_RUN_LEASE_TTL_SECONDS` (default 300 s) |
| `{NS}:chat_session:{id}:cancel` | Cross-instance cancel signal | `REDIS_CANCEL_SIGNAL_TTL_SECONDS` (default 120 s) |
| `{NS}:chat_session:{id}:run_trace` | W3C traceparent for OTel span reattach | Same as lease TTL |
| `{NS}:gate_response:{id}:{name}` | Per-responder gate input | `_GATE_RESPONSE_TTL` |
| `{NS}:gate_winner:{id}` | First-win / all-quorum race lock | `_GATE_RESPONSE_TTL` |
| `{NS}:pending_task:{id}` | Quorum-composed task for next `/run/` | `_PENDING_TASK_TTL` |
| `{NS}:mcp_oauth:run:{id}:{server}:token` | Session-scoped MCP OAuth Bearer token | JWT `exp` claim (floor 60 s) |
| `{NS}:mcp_oauth_state:{state}:meta` | PKCE state for OAuth Authorization Code flow | Short-lived |
| `{NS}:mcp_oauth:test:{project}:{server}:status` | Credential test status | Short-lived |
