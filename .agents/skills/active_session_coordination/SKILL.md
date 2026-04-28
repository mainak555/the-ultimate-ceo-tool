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
