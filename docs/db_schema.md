# DB Schema — Canonical Reference

## Enforcement Rule

**Attribute names must be identical across all layers.** This file is the single source of
truth. Before adding or renaming any field, update this document first, then propagate the
exact same name through every layer listed below:

| Layer | Location | How it uses the name |
|-------|----------|----------------------|
| **DB document** | MongoDB `project_settings` | Stored key |
| **Validation** | `server/schemas.py` — `validate_team()`, `validate_agent()` | `data.get("<name>")` and `cleaned["<name>"]` |
| **Normalisation** | `server/services.py` — `normalize_project()` | `raw_team.get("<name>", …)` / `raw_agent.get("<name>", …)` |
| **Form parsing** | `server/views.py` — `_build_project_data()` | `post_data.get("team[<name>]")` or `post_data.get("agents[n][<name>]")` |
| **Templates** | `server/templates/server/partials/config_form.html`, `config_readonly.html`, `_agent_card.html` | `name="team[<name>]"` / `project.team.<name>` |
| **Runtime** | `agents/team_builder.py` — `build_team()`, `build_agent_runtime_spec()` | `team_cfg.get("<name>")` / `agent_config.get("<name>")` |

Cross-references: [docs/API.md](API.md) (form fields + HTTP schema), [AGENTS.md](../AGENTS.md) rules 1–5, [docs/integrations.md](integrations.md) (integrations sub-document).

---

## Collection: `project_settings`

```jsonc
{
  // ── Top-level ─────────────────────────────────────────────────────────────
  "project_name": "string (unique, used as URL slug)",
  "objective":    "string — injected into every agent system prompt and selector system prompt at runtime",

  // ── Assistant agents ──────────────────────────────────────────────────────
  "agents": [
    {
      "name":          "string — must be a valid Python identifier",
      "model":         "string — must match an entry in agent_models.json",
      "system_prompt": "string — non-empty; project objective is appended at runtime",
      "temperature":   0.7   // float, 0.0–2.0
    }
    // ...one entry per assistant agent
  ],

  // ── Human gate (optional) ─────────────────────────────────────────────────
  "human_gate": {
    "enabled":          true,           // bool
    "name":             "string",       // required when enabled=true
    "interaction_mode": "approve_reject" // "approve_reject" | "feedback"
  },

  // ── Team ──────────────────────────────────────────────────────────────────
  "team": {
    "type":           "round_robin",  // "round_robin" | "selector"
    "max_iterations": 5,              // int ≥ 1; ≤ 10 when human gate disabled

    // ── Selector-only fields (omitted / ignored for round_robin) ────────────
    "model":         "string — must match an entry in agent_models.json",
    "system_prompt": "string — routing instructions; {roles}, {history}, {participants} expanded by AutoGen",
    "temperature":   0.0,  // float, 0.0–2.0; 0.0 = deterministic routing (recommended)
    "allow_repeated_speaker": true  // bool
  },

  // ── Integrations (optional) ───────────────────────────────────────────────
  "integrations": {
    "enabled": false,           // bool — master toggle

    // ── Trello ────────────────────────────────────────────────────────────
    "trello": {
      "enabled": false,         // bool
      "export_agents": [],      // list[str] — agent names whose messages show the Export button;
                                //             empty list = show on all agents' messages
      "app_name": "string",     // required when enabled
      "api_key": "string",      // required when enabled — stored encrypted at rest
      "token": "string",        // Trello token (expiration=never), masked in UI via SECRET_MASK
      "token_generated_at": "string",  // ISO 8601 UTC datetime when token was generated
      "default_workspace_id": "",  // Trello workspace/organization ID
      "default_workspace_name": "", // display name (for readonly view)
      "default_board_id": "",      // Trello board ID
      "default_board_name": "",    // display name
      "default_list_id": "",       // Trello list ID
      "default_list_name": "",     // display name
      "export_mapping": {
        "model": "",          // optional — blank = fall back to first assistant agent's model
        "temperature": 0.0,   // float 0.0–2.0; 0.0 = deterministic extraction (default)
        "system_prompt": "string"  // extraction prompt given to the LLM
      }
    }
    // Future integrations (e.g. Jira) follow the same pattern:
    // "jira": { "enabled": false, "export_agents": [], ... }
  }
}
```

---

## Collection: `chat_sessions`

```jsonc
{
  "_id": "ObjectId",
  "project_id": "string (project ObjectId hex)",
  "description": "string",
  "created_at": "datetime",
  "discussions": [
    {
      "id": "uuid string (required for export context)",
      "agent_name": "string",
      "role": "user | assistant",
      "content": "string",
      "timestamp": "HH:MM",
      "exports": {
        "trello": {
          "schema_version": "string",
          "updated_at": "ISO datetime",
          "source": "extract | manual",
          "cards": [],
          "last_push": {
            "pushed_at": "ISO datetime",
            "list_id": "string",
            "result": []
          }
        }
        // future providers: jira, pdf, n8n (same top-level shape with provider-specific payload details)
      }
    }
  ],
  "status": "idle | running | awaiting_input | completed | stopped",
  "current_round": 0,
  "agent_state": {
    "source": "string",
    "version": "string",
    "saved_at": "ISO datetime",
    "state": {}
  }
}
```

### Discussion Export Persistence Rule

- Raw reference content for export modals is always `discussions[].content`.
- Export payload persistence is provider-scoped under `discussions[].exports.<provider>`.
- This separation allows re-extract/save/push workflows without mutating source discussion text.

Indexes:
- `{ project_id: 1 }` (non-unique)
- `{ _id: 1, "discussions.id": 1 }` unique (`uniq_session_discussions_id`) with partial filter on string `discussions.id`
  This guarantees `discussions.id` uniqueness within a single session while allowing the same id in different sessions.

---

## Field Notes

### `agents[].name`
Stored exactly as the sanitised Python identifier produced by `validate_agent()`. Spaces
and hyphens are converted to underscores; all other non-word characters are stripped.

### `agents[].temperature` vs `team.temperature`
Both fields are named `temperature` — one lives under each agent, the other under `team`
(selector only). They are independent: agent temperature controls generation quality;
selector temperature controls routing determinism.

### `team.model` / `team.system_prompt` (selector only)
These fields share the same names as the per-agent fields intentionally — the selector is
logically "the routing agent". The `selector_` prefix was dropped to maintain consistent
naming across agent and team layers.

### `team.max_iterations`
Stored on `team`, not at the top level. `normalize_project()` falls back to the legacy
top-level `max_iterations` for backward compatibility with old documents.

### `integrations.trello.export_agents`
Stored as a list of agent name strings. An empty list means all agents' messages will show
the Trello Export button. Legacy documents may have `integrations.export_agent` (a single
string) instead — `_normalize_export_agents()` in `services.py` migrates that on read
without requiring a DB migration script.

Each future integration (e.g. `jira`) stores its own `export_agents` list at the same
depth: `integrations.jira.export_agents`. The `integrations` root never holds an
`export_agent` field in new documents.

---

## Validation Constraints

| Field | Type | Constraint |
|-------|------|------------|
| `project_name` | str | non-empty, unique in collection |
| `objective` | str | any string (may be empty) |
| `agents[].name` | str | valid Python identifier after sanitisation |
| `agents[].model` | str | must be in `agent_models.json` |
| `agents[].system_prompt` | str | non-empty |
| `agents[].temperature` | float | 0.0 ≤ value ≤ 2.0 |
| `human_gate.interaction_mode` | str | `"approve_reject"` or `"feedback"` |
| `human_gate.name` | str | required when `enabled=true` |
| `team.type` | str | `"round_robin"` or `"selector"` |
| `team.max_iterations` | int | ≥ 1; ≤ 10 when `human_gate.enabled=false` |
| `team.model` | str | required for selector; must be in `agent_models.json` |
| `team.system_prompt` | str | required for selector; non-empty |
| `team.temperature` | float | 0.0 ≤ value ≤ 2.0 (default `0.0`) |
| `team.allow_repeated_speaker` | bool | default `true` |

---

## MongoDB Migration Notes

When renaming a field, issue an `updateMany` on the collection before deploying:

```js
// Example: renaming selector_model → model and selector_prompt → system_prompt
db.project_settings.updateMany(
  { "team.type": "selector" },
  [{
    $set: {
      "team.model":         "$team.selector_model",
      "team.system_prompt": "$team.selector_prompt",
      "team.temperature":   { $ifNull: ["$team.temperature", 0.0] }
    }
  }]
);
db.project_settings.updateMany(
  { "team.type": "selector" },
  { $unset: { "team.selector_model": "", "team.selector_prompt": "" } }
);
```

There is **no automatic backward-compat fallback** in `normalize_project()`. Any document
that still uses old field names will render empty strings for those fields until migrated.
