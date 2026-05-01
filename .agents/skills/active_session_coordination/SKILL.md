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

### `run_stream()` task guard (Anthropic prefill rule)

**All Anthropic Claude 3.7+ / Claude 4+ models reject a conversation whose last message is an
`AssistantMessage`** (the "prefill" pattern was removed).  `views.py` enforces this by setting
`effective_task` to `"Continue."` whenever the resolved task is falsy **before every call to
`team.run_stream()`**, without any `is_first_run` exemption:

- **Empty gate resume (multi-assistant)**: user submits Continue with no text — no `UserMessage`
  would be added to agent contexts without the guard.
- **Failed attachment extraction on any run**: all PDF/DOCX text extraction fails (e.g. scanned
  PDF), collapsing `task_for_agent` to an empty string; `run_stream(task=None)` would produce
  an empty message list rejected by the API.

The injected `"Continue."` is **not persisted** to `discussions[]` and **not shown** in the
SSE stream.  It is written into `agent_state` checkpoints, which correctly records the resume
turn in model context.

**Do not add an `is_first_run` exception** to this guard.  Attachment extraction can fail on
the first run too, making the guard necessary for all code paths.

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
- Calling `team.run_stream(task=None)` or `run_stream(task="")` — always ensure a non-empty
  `effective_task` before calling `run_stream()`. Falsy tasks may produce an empty message
  list that Anthropic and other strict providers reject with a 400 error.
- Adding an `is_first_run` exception to the `effective_task` guard — attachment extraction
  can fail on the first run, making the guard needed unconditionally.
