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

### `build_team(project)`

Reads `project["team"]["type"]` and builds the appropriate AutoGen team.

#### Termination strategy (both team types)

| `human_gate.enabled` | Termination |
|----------------------|-------------|
| `false` | `MaxMessageTermination(n_agents × max_iterations)` — runs all rounds automatically |
| `true` | `MaxMessageTermination(n_agents)` — stops after one full round; caller resumes per round |

---

## Team Types

### `round_robin`

Uses `RoundRobinGroupChat`. Agents speak in the fixed order they are listed in the project configuration. No routing model required.

**Config fields used:**
- `team.max_iterations`
- `human_gate.enabled`

---

### `selector`

Uses `SelectorGroupChat`. A dedicated model client selects the next speaker each turn based on the selector prompt, conversation history, and agent descriptions.

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

## Runtime Cache — `runtime.py`

Teams are kept alive in a process-local dict (`_TEAM_CACHE`) keyed by `session_id`. This preserves AutoGen's internal conversation history between rounds in human-gated runs.

The runtime also persists native AutoGen team state to `chat_sessions.agent_state` using:

- `await team.save_state()` at run checkpoints
- `await team.load_state(saved_state)` on cache-miss restore

```
session_id → AutoGen team instance
session_id → CancellationToken
```

### Key functions

| Function | When to call |
|----------|-------------|
| `get_or_build_team(session_id, project)` | Before every `run_stream()` call. Builds on miss, returns cached on hit |
| `reset_cancel_token(session_id)` | Before every `run_stream()` call to issue a fresh token |
| `cancel_team(session_id)` | To stop a running stream (e.g. user cancels) |
| `evict_team(session_id)` | After session completes or is abandoned; frees memory |

### Cache lifetime

- **Cache miss**: Fresh team is built from `project` config via `build_team()`.
- **Cache hit**: Existing team is reused with its accumulated history intact.
- **Server restart**: Cache is lost. If persisted `agent_state` exists, the team is rebuilt and `load_state()` restores it.
- **State mismatch**: If `load_state()` fails due to schema/version drift, restart is rejected with an explicit "state version mismatch" error (no fallback rebuild path).

---

## Human Gate Flow

The human gate pauses execution after each full round (`n_agents` messages). The `views.chat_session_run` SSE handler manages the state machine:

```
idle ──► running ──► awaiting_input ──► running ──► ... ──► completed
                         ▲                  │
                         └──── (resume) ────┘
```

- **Continue**: POST `/chat/sessions/<id>/respond/` with `action=continue` and optional `text`. The server returns `{status:"ok", task:"..."}` and the UI calls `/run/` with that task.
- **Decision + Notes**: `Approve` / `Reject` are optional shortcuts. If clicked, the UI prepends `APPROVED` or `REJECTED` followed by a blank line in the notes textarea. Continue can still be sent without selecting either shortcut. Any non-empty continue text is persisted as a `user` role entry in `discussions` and passed to `run_stream(task=...)`.
- **Stop**: POST `/chat/sessions/<id>/respond/` with `action=stop` transitions session to `stopped` and evicts the cached team.
- **First run**: `task` must be non-empty — a 400 is returned if `discussions` is empty and no task was provided.

---

## Adding a New Team Type

1. Add the type string to `TEAM_TYPES` in `server/schemas.py`.
2. Add a validation branch in `validate_team()` for any new config fields.
3. Pass new fields through in `normalize_project()` in `server/services.py`.
4. Add a build branch in `build_team()` in `agents/team_builder.py`.
5. Add a `<option>` to the `team_type` select in `config_form.html` and show/hide any new fields via `syncTeamTypeFields()` in `app.js`.
6. Update `docs/API.md` (form fields + MongoDB schema) and this file.
