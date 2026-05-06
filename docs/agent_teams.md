# Agent Teams — Runtime Reference

This document covers how AutoGen teams are assembled and run from saved project configuration.
See [docs/agent_factory.md](agent_factory.md) for model client construction and provider env variables.

## Module Overview

| Module | Responsibility |
|--------|---------------|
| `agents/prompt_builder.py` | Resolves agent system messages, injects project objective |
| `agents/team_builder.py` | Builds `AssistantAgent` instances and the team from config |
| `agents/runtime.py` | Process-local team cache, cancellation tokens, session lifecycle |

---

## Prompt Resolution — `prompt_builder.py`

```python
resolve_system_prompt(system_prompt: str, objective: str = "") -> str
```

- `system_prompt` is used as-is. Schemas enforce it is non-empty before reaching runtime.
- If `objective` is non-empty, it is **appended** after the persona content:

```
<system_prompt content>

---
Project Objective:
<objective>
```

Objective is appended (not prepended) so **line 1 of the system prompt always remains the agent's identity anchor**. This line is also used as the agent's `description` for selector routing (see below).

---

## Team Construction — `team_builder.py`

### `build_agent_runtime_spec(agent_config, objective="")`

Converts a single saved agent dict into a runtime spec:

| Output key | Source |
|------------|--------|
| `name` | `agent_config["name"]` (sanitised to a valid Python identifier) |
| `model_client` | `build_model_client(agent_config["model"], temperature=...)` |
| `system_message` | `resolve_system_prompt(agent_config["system_prompt"], objective)` |
| `description` | Line 1 of the resolved `system_message` |

`description` is consumed by `SelectorGroupChat`'s `{roles}` placeholder (see Selector Prompt below). For `RoundRobinGroupChat` it has no runtime effect but is always populated for future-proofing.

### `build_team(project, session_id=None, remote_users=None)`

Reads `project["team"]["type"]` and builds the appropriate AutoGen team.

- `remote_users` — list of remote user dicts `{name, description}` taken from the **session snapshot** (`chat_sessions.remote_users`), never directly from the project. Passed in from `get_or_build_team`. When `quorum == "team_choice"` and this list is non-empty, `UserProxyAgent` instances are added after all `AssistantAgent` instances. See [Remote Users & Quorum](#remote-users--quorum) below.

#### Termination strategy

All termination uses a custom `AgentMessageTermination` class (defined in `team_builder.py`)
that counts only messages where `source != "user"`. This avoids the off-by-one present in
AutoGen's built-in `MaxMessageTermination`, which counts the initial task message and would
consume one agent turn on every `run_stream()` call.

| `human_gate.enabled` | Termination |
|----------------------|-------------|
| `false` | `AgentMessageTermination(n_agents × max_iterations)` — runs all rounds automatically |
| `true` | `AgentMessageTermination(n_agents) \| ExternalTermination()` — stops after one full agent round or when Stop is pressed (graceful); see Stop Mechanism below |

`n_agents` counts **all participants** — `AssistantAgent` instances plus any `UserProxyAgent` instances added for `quorum == "team_choice"` (see below). This ensures `AgentMessageTermination` fires after one full round including proxy turns.

---

## Remote Users & Quorum

### Overview

A project can define `human_gate.remote_users` — additional human participants beyond the gate owner. Three quorum modes control how their responses combine to form the agent task and resume the run:

| `quorum` | Meaning |
|----------|---------|
| `"na"` | No remote users (forced automatically by `validate_human_gate` when `remote_users == []`) |
| `"first_win"` | First responder wins; all subsequent POSTs return 409 |
| `"all"` | All configured participants must respond before the run resumes; inputs are merged into one composed task |
| `"team_choice"` | Remote users participate as `UserProxyAgent` nodes inside the AutoGen team; selector routing chooses them as needed |

### Session Snapshot Contract

`remote_users` and `quorum` are **always** read from the live `project["human_gate"]` at runtime. Neither is stored in `chat_sessions`. This means:
- Team config changes (adding/removing remote users, changing quorum mode) take effect on the next run without creating a new session.
- `event_stream` and `chat_session_respond` always load the project and read `remote_users` from it.

### Remote disconnect during active run (deferred readiness latch)

When a configured remote participant disconnects while `session.status == "running"`:

- The current run is not interrupted.
- A Redis deferred-readiness latch is set for that session.
- The next `POST /chat/sessions/<id>/run/` is blocked behind the existing
   `awaiting_remote_users` readiness path until required participants are
   satisfied (`online` or `ignored`).

Scope rules:

- Applies only to configured remote users.
- Does not apply to guest watchers.
- Does not trigger for users already marked `ignored`.

### `quorum == "team_choice"` — UserProxyAgent Wiring

`build_team()` creates one `UserProxyAgent` per remote user when `quorum == "team_choice"` and `remote_users` is non-empty:

```python
UserProxyAgent(
    name=safe_name,                            # sanitized with same re.sub as AssistantAgent
    description=ru["description"] or "Remote participant",
    input_func=placeholder_input_func,         # async, returns "Continue." immediately
)
```

**Phase 1 placeholder**: `input_func` is an async closure that returns `"Continue."` immediately — the run is never blocked. When `UserInputRequestedEvent` appears in the SSE stream, `event_stream()` emits a `remote_input_requested` SSE breadcrumb `{proxy_name, request_id}` for future WebSocket integration.

**Phase 1 limitation**: real remote user input is not delivered to the proxy. Full WebSocket/Redis pub-sub delivery is deferred to a later phase.

### `quorum == "all"` and `quorum == "first_win"` — Gate-Level Quorum

No `UserProxyAgent` is added to the team. Quorum is managed programmatically in `chat_session_respond()` using Redis per-user keys. See [active_session_coordination skill](./../.agents/skills/active_session_coordination/SKILL.md) and [remote_user_quorum skill](./../.agents/skills/remote_user_quorum/SKILL.md) for the full contract.

**Composed task** (quorum=all): all non-empty responder texts joined with `"\n\n"` in `expected_names` order (gate user first, then remote users in project config order). The composed task is stored in Redis (`pending_task`) and consumed by the next `/run/` call.

**Discussion entries**: one entry per responder is inserted into `discussions[]` in `expected_names` order immediately when quorum is met — before the session is set to `idle`. Each entry follows the standard discussion shape (`agent_name`, `role=user`, `content`, `timestamp`, `attachments`).

AutoGen automatically calls `termination.reset()` after each round fires, so `AgentMessageTermination._count`
and `ExternalTermination._setted` both reset cleanly between gate rounds without manual intervention.

#### Stop mechanism (human-gated runs)

When the user presses **Stop**:
1. `cancel_team(session_id)` calls `ExternalTermination.set()` first — graceful signal.
2. `CancellationToken.cancel()` follows — hard interrupt for any in-flight LLM call.
3. The graceful signal fires at the next turn boundary; the current agent message (if any) is
   fully written before `TaskResult` is yielded, so no message is lost.
4. `evict_team(session_id)` removes the team and `ExternalTermination` from all caches.

---

## Team Types

### `round_robin`

Uses `RoundRobinGroupChat`. Agents speak in the fixed order they are listed in the project configuration. No routing model required.

**Config fields used:**
- `team.max_iterations`
- `human_gate.enabled`

For single-assistant projects, `RoundRobinGroupChat` is the only valid team runtime.

---

### `selector`

Uses `SelectorGroupChat`. A dedicated model client selects the next speaker each turn based on the selector prompt, conversation history, and agent descriptions.

`SelectorGroupChat` requires at least two participants.

**Config fields used:**

| Field | Default | Description |
|-------|---------|-------------|
| `team.model` | — (required) | Model used exclusively for routing decisions. Built at `temperature` value for speaker selection |
| `team.system_prompt` | — (required) | Routing instructions; see Selector Prompt section |
| `team.temperature` | `0.0` | Temperature for the selector model client. `0.0` = deterministic routing (recommended) |
| `team.allow_repeated_speaker` | `true` | Whether the same agent can be selected consecutively |
| `team.max_iterations` | `5` | Used to compute termination message count |

**Objective injection into the selector prompt:**

The project `objective` is **prepended** to the user-supplied selector prompt before the team is built:

```
Project Objective:
<objective>

<user system_prompt>
```

Objective is prepended here (not appended) because the selector prompt has no role line — the objective must ground routing decisions before `{roles}` and `{history}` are expanded.

---

## Selector Prompt Placeholders

AutoGen expands three placeholders inside the selector prompt at each turn:

| Placeholder | Expands to |
|-------------|-----------|
| `{roles}` | `"AgentName: <description>"` for each agent, newline-separated. `description` comes from line 1 of each agent's system message |
| `{history}` | Full conversation history so far, formatted as `source: content` lines |
| `{participants}` | Comma-separated list of agent names; used in the instruction to reply with one name only |

**Example selector prompt:**

```
Select an agent to perform the next task.

{roles}

Current conversation context:
{history}

Read the above conversation, then select an agent from {participants} to perform the next task.

Routing guidelines:
- Select the agent whose role and expertise best matches the current sub-task.
- Do not select the same agent consecutively unless no other agent is appropriate.
- If the conversation has just started, select the agent best suited to decompose or initiate the task.
- If the current agent has finished their contribution, select the next most relevant agent.

Only select one agent. Reply with the agent name only.
```

The default example is stored in `server/model_catalog.SELECTOR_AGENT_PROMPT` and shown as a placeholder hint in the UI.

---

## `AgentMessageTermination` — Custom Condition

Defined in `agents/team_builder.py`. Replaces AutoGen's `MaxMessageTermination` everywhere in this project.

**Why it exists**: `MaxMessageTermination` counts every `BaseChatMessage`, including the
initial `TextMessage(source="user")` task. With `MaxMessageTermination(N)`, only `N-1`
agent turns occur per round. `AgentMessageTermination(N)` filters to messages where
`source != "user"`, giving exactly `N` agent turns.

**Interface** (duck-type compatible with `TerminationCondition`):
- `terminated: bool` — true once the limit is reached
- `async __call__(messages) -> StopMessage | None` — accumulates count, returns `StopMessage` when limit is hit
- `async reset() -> None` — resets count to 0 (called automatically by AutoGen when fired)
- `__or__(other) -> _Or` — supports `AgentMessageTermination(N) | ExternalTermination()` syntax

---

## Runtime Cache — `runtime.py`

Teams are kept alive in a process-local dict (`_TEAM_CACHE`) keyed by `session_id`. This preserves AutoGen's internal conversation history between rounds in human-gated runs.

Active run ownership is coordinated through Redis (`agents/session_coordination.py`):

- One active run lease per `session_id`.
- Lease heartbeat renewal while SSE streaming is active.
- Cross-instance cancel signal used by `/chat/sessions/<id>/stop/`.
- Run start is fail-fast when Redis is unavailable.

The runtime also persists native AutoGen team state to `chat_sessions.agent_state` using:

- `await team.save_state()` at run checkpoints
- `await team.load_state(saved_state)` on cache-miss restore

```
session_id → AutoGen team instance
session_id → CancellationToken
session_id → ExternalTermination  (gated runs only; graceful stop signal)
```

### Key functions

| Function | When to call |
|----------|-------------|
| `get_or_build_team(session_id, project, remote_users=None)` | Before every `run_stream()` call. Builds on miss, returns cached on hit. Stashes `ExternalTermination` on cache miss. Passes `remote_users` through to `build_team()` for `team_choice` proxy construction |
| `reset_cancel_token(session_id)` | Before every `run_stream()` call to issue a fresh `CancellationToken`. `ExternalTermination` resets automatically when fired |
| `cancel_team(session_id)` | To stop a running stream. Calls `ExternalTermination.set()` (graceful) then `CancellationToken.cancel()` (hard fallback) |
| `evict_team(session_id)` | After session completes or is abandoned; clears `_TEAM_CACHE`, `_CANCEL_TOKENS`, `_EXTERNAL_TERMINATIONS`, and MCP workbenches |

### Cache lifetime

- **Cache miss**: Fresh team is built from `project` config via `build_team()`.
- **Cache hit**: Existing team is reused with its accumulated history intact.
- **Server restart**: Cache is lost. If persisted `agent_state` exists, the team is rebuilt and `load_state()` restores it.
- **State mismatch**: If `load_state()` fails due to schema/version drift, restart is rejected with an explicit "state version mismatch" error (no fallback rebuild path).

Redis coordination keys are ephemeral and are not used for resume state.
Durable resume data always comes from MongoDB `chat_sessions.agent_state`.

### Horizontal scaling

`_TEAM_CACHE` and `_CANCEL_TOKENS` are **process-local**. AutoGen team objects
contain live asyncio tasks, agent instances, and MCP workbench connections —
they cannot be serialised to Redis, Memcached, or any shared store.

**Required: session-affinity (sticky sessions) at the load balancer / ingress.**

Every SSE streaming request and every HITL resume POST for a given `session_id`
must be routed to the same container instance. Without stickiness, a resume
request landing on a different replica causes a cache miss, the team is rebuilt
from scratch, and the in-progress turn counter resets.

| Deployment | Sticky session mechanism |
|---|---|
| **Nginx** | `ip_hash;` or `hash $cookie_sessionid consistent;` in upstream block |
| **Docker Compose (multi-replica)** | Add `nginx` reverse proxy with `ip_hash` in front of scaled `app` replicas |
| **Kubernetes Ingress (nginx-ingress)** | `nginx.ingress.kubernetes.io/affinity: "cookie"` + `nginx.ingress.kubernetes.io/session-cookie-name: "SERVERID"` annotations |
| **Kubernetes Service** | `sessionAffinity: ClientIP` on the `ClusterIP` Service (coarser — IP-level only) |
| **AWS ALB** | Target group stickiness enabled with duration-based cookies |

**Cancel across instances** still works without stickiness: the stop endpoint
writes a Redis cancel key; the owning container's heartbeat loop polls this key
and calls `cancel_team()` locally via `session_coordination.py`.

**Crash / restart recovery:**

1. Owning container dies — Redis lease expires after `REDIS_RUN_LEASE_TTL_SECONDS`.
2. Next request for the session arrives on any instance — cache miss.
3. `get_or_build_team()` builds a fresh team; `load_state()` restores it from
   MongoDB `chat_sessions.agent_state` checkpoint.
4. New instance acquires the Redis lease and resumes SSE streaming.

> **Do not use Memcached as an alternative to Redis.** Memcached has no
> server-side scripting, no atomic CAS, and no Lua — the lease/heartbeat
> atomicity in `session_coordination.py` requires Redis.

---

## Human Gate Flow

The `views.chat_session_run` SSE handler manages the state machine:

```
idle ──► running ──► awaiting_input ──► running ──► ... ──► completed
                         ▲                  │
                         └──── (resume) ────┘
```

- **Continue**: POST `/chat/sessions/<id>/respond/` with `action=continue` and optional `text`. The server returns `{status:"ok", task:"..."}` and the UI calls `/run/` with that task.
- **Gate UI — unified input bar**: when the SSE `gate` event fires, a non-interactive `.chat-status-badge--gate` is appended to chat, and the bottom input bar switches to gate mode (`setGateMode(data)`). The placeholder updates to show the round, the Stop button stays visible, and the Send button routes to `_handleGateSend()` which calls `sendRespond("continue", ...)`. No separate gate panel widget is injected. The `Approve` / `Reject` decision shortcuts have been removed — users type their response directly.
- **Empty Continue (multi-assistant mode)**: When Continue is submitted with no text, `task` is empty and no `UserMessage` is broadcast by AutoGen, leaving each agent's model context ending with its own prior `AssistantMessage`. Anthropic Claude 4+ models reject this (they no longer support "assistant prefill"). `views.py` therefore injects a synthetic `"Continue."` task for `run_stream()` in this case. The synthetic message is **not persisted** to `discussions[]` and **not shown** in the SSE chat stream (filtered because `source == "user"` is excluded from SSE output messages). It is baked into `agent_state` checkpoints, which is correct — the model context accurately records the resume event.
- **Stop**: POST `/chat/sessions/<id>/respond/` with `action=stop` transitions session to `stopped` and evicts the cached team. In gate mode, the Stop button (`#chat-stop-btn`) calls `sendRespond("stop")` via the respond endpoint. During an active run it calls the dedicated `/stop/` endpoint (fire-and-forget).
- **First run**: `task` must be non-empty — a 400 is returned if `discussions` is empty and no task was provided.
- **Page-reload / session-switch recovery**: when an `awaiting_input` session is loaded, `chat_session_history.html` renders a `.chat-status-badge--gate` with `data-gate-context` JSON. **Two paths** restore gate state — both scan for the badge and call `setGateMode(ctx)`:
  - `DOMContentLoaded` bootstrap (initial page render — runs once at script start).
  - `htmx:afterSwap` handler (session switch via sidebar — runs on every HTMX history swap).
  Neither path makes an extra API call.
- **Gate badge format**: `⏸ Round N/M — response is required` for multi-agent or single-assistant with remote users; `⏸ Round N — response is required` for pure single-assistant chat mode (no iteration limit, so no `/M` denominator). The placeholder also updates to show the round.
- **SSE pump stop-button guard**: the `pump()` loop in `_doStartRun()` checks `if (!_gateData) setRunningState(false)` before hiding the Stop button when the SSE stream closes (`result.done`) or errors. This prevents the `result.done` callback — which fires immediately after the `gate` frame — from re-hiding the Stop button that `setGateMode()` just showed.
- **Restart panel + Send interaction**: when `.chat-restart-panel` is present in chat history (stopped/completed session), the `chatSendBtn` handler clears the session ID and forces the create-session path. The user's text starts a **new** session and run. The restart panel's own `data-session-id` continues to own "Continue from last" / "Add context and continue" independently.

Mode-specific pause behavior:

- **Multi-assistant (`n_agents >= 2`)**: gate pauses after each full round and completion can occur when `current_round` reaches `max_iterations`.
- **Single-assistant, no remote users (`n_agents == 1`, `remote_users == []`) — pure chat mode**: gate pauses after every assistant turn and does not auto-complete via `max_iterations`; the human `Stop` action controls termination. Empty Continue (no text, no attachments) is rejected with HTTP 400.
- **Single-assistant with remote users (`n_agents == 1`, `len(remote_users) >= 1`)**: behaves like multi-assistant — team config is honored and `max_iterations` governs run completion. Empty Continue is allowed. Team Setup is visible in config UI.
- **`quorum == "team_choice"` with `UserProxyAgent` participants**: proxies are counted in `n_agents`. `AgentMessageTermination` fires after one full round that includes proxy turns. The `is_single_assistant_gate`/`is_single_assistant_chat_mode` check uses `session["remote_users"]` (session snapshot), so this mode is never mistakenly classified as pure single-assistant chat mode.
- **Remote quorum runtime semantics**:
   - `first_win`: first accepted responder (host or remote) commits the gate round; host UI auto-replays `/run/` from Redis pending-task handoff.
   - `all`: host waits until all expected responders submit, then host final Continue commits and resumes.
   - Late responder conflicts return `stale` / `locked` race outcomes and should be treated as already-committed state.

---

## Adding a New Team Type

1. Add the type string to `TEAM_TYPES` in `server/schemas.py`.
2. Add a validation branch in `validate_team()` for any new config fields.
3. Pass new fields through in `normalize_project()` in `server/services.py`.
4. Add a build branch in `build_team()` in `agents/team_builder.py`.
5. Add a `<option>` to the `team_type` select in `config_form.html` and show/hide any new fields via `syncTeamTypeFields()` in `app.js`.
6. Update `docs/API.md` (form fields + MongoDB schema) and this file.
