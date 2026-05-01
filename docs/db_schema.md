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
  "created_at":   "datetime (UTC BSON Date — set on insert, never overwritten)",
  "updated_at":   "datetime (UTC BSON Date — stamped on every replace_one)",

  // ── Assistant agents ──────────────────────────────────────────────────────
  "agents": [
    {
      "name":              "string — must be a valid Python identifier",
      "model":             "string — must match an entry in agent_models.json",
      "system_prompt":     "string — non-empty; project objective is appended at runtime",
      "temperature":       0.7,   // float, 0.0–2.0
      "mcp_tools":         "none",  // "none" | "shared" | "dedicated"
      "mcp_configuration": {}        // {} unless mcp_tools == "dedicated"; see docs/mcp_integration.md
    }
    // ...one entry per assistant agent
  ],

  // ── Project-level MCP shared config ───────────────────────────────────────
  "shared_mcp_tools": {},  // {} or {"mcpServers": {...}} — required non-empty when any agent uses mcp_tools = "shared"

  // ── Human gate (optional) ─────────────────────────────────────────────────
  "human_gate": {
    "enabled":      true,        // bool
    "name":         "string",    // required when enabled=true (the local "leader" gate name)
    "quorum":       "yes",       // "yes" | "first_win" | "team_config"
                                 //   yes        — wait for all enabled remote users to reply
                                 //   first_win  — first remote response continues the run
                                 //   team_config — agent team (Selector) decides who must reply
                                 // Legacy bool tolerated on read: True→"yes", False→"first_win"
    "remote_users": [            // [] when enabled=false; multi-assistant only
      {
        "id":          "uuid",   // server-minted, preserved across saves
        "name":        "string", // unique within the list, non-empty
        "description": "string"  // shown to the agent team and used by Selector routing
      }
      // Per-user enable/disable is a runtime concern (lobby/readiness in Phase 2),
      // not a stored config field.
    ]
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
      "token_generated_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
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
    },

    // ── Jira (three independent types) ───────────────────────────────────
    "jira": {
      "enabled": false,         // bool — master toggle
      "software": {
        "enabled": false,
        "site_url": "",            // required when enabled
        "email": "",               // required when enabled
        "api_key": "",             // required when enabled
        "default_project_key": "",
        "default_project_name": "",
        "export_agents": [],        // empty = show on all agents
        "export_mapping": {
          "model": "",
          "temperature": 0.0,
          "system_prompt": ""
        }
      },
      "service_desk": {
        // same structure as software
      },
      "business": {
        // same structure as software
      }
    }
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
      "timestamp": "datetime (UTC BSON Date)",
      "attachments": [
        {
          "id": "uuid string",
          "filename": "string",
          "mime_type": "string",
          "extension": "string",
          "size_bytes": 0,
          "is_image": true,
          "content_url": "string (session-scoped endpoint)",
          "thumbnail_url": "string (image only)",
          "uploaded_at": "datetime (UTC BSON Date)"
        }
      ],
      "exports": {
        "trello": {
          "schema_version": "string",
          "updated_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
          "exported": true,
          "source": "extract | manual",
          "cards": [],
          "last_push": {
            "pushed_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
            "list_id": "string",
            "result": []
          }
        },
        "jira": {
          "software": {
            "schema_version": "string",
            "updated_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
            "exported": true,
            "source": "extract | manual",
            "issues": [],
            "last_push": {
              "pushed_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
              "project_key": "string",
              "result": []
            }
          },
          "service_desk": {
            // same top-level shape as jira.software; issues follow service desk schema
          },
          "business": {
            // same top-level shape as jira.software; issues follow business schema
          }
        }
        // future providers: pdf, n8n
      }
    }
  ],
  "status": "idle | running | awaiting_input | completed | stopped",
  "current_round": 0,
  "agent_state": {
    "source": "string",
    "version": "string",
    "saved_at": "datetime (UTC BSON Date — coerced to ISO string on read)",
    "state": {}  // AutoGen TeamState JSON — do not modify structure
  }
}
```

## Collection: `chat_attachments`

```jsonc
{
  "_id": "ObjectId",
  "attachment_id": "uuid string",
  "project_id": "string (project ObjectId hex)",
  "session_id": "string (chat session ObjectId hex)",
  "message_id": "string | null (bound discussion message id)",
  "staging_message_id": "string",
  "filename": "string",
  "extension": "string",
  "mime_type": "string",
  "size_bytes": 0,
  "is_image": true,
  "blob_key": "sessions/<session_id>/attachments/<attachment_id>/<filename>",
  "uploaded_at": "datetime (UTC BSON Date)",
  "bound_at": "datetime (UTC BSON Date)"
  // NOTE: extracted_text and extraction_status are NEVER written here.
  // Text extraction is lazy-cached in Redis only; see attachment_storage.md.
}
```

Attachment storage/delete contract:

- Blob/object keys are strictly session-scoped by prefix `sessions/<session_id>/...`.
- On chat session delete, all blobs under `sessions/<session_id>/` and all `chat_attachments` metadata rows for that session are deleted together.

`exports.trello.exported` lifecycle:
- `false` after Extract Items or Save (editable mode).
- `true` after successful Export to Trello push (summary-only locked mode).

### Discussion Export Persistence Rule

- Raw reference content for export modals is always `discussions[].content`.
- For human (`role=user`) messages, `discussions[].content` stores **only the raw user-typed text** — never the `text_with_context` string that includes extracted PDF/DOCX/etc. attachment content. Extracted attachment text is a runtime artefact rebuilt from Blob → Redis on each run.
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

When assistant count is exactly 1 (single-assistant chat mode), the persisted
`team` object may be omitted entirely. Runtime falls back to Round Robin behavior
and human-gated `Continue`/`Stop` controls loop progression and termination.

### `integrations.trello.export_agents`
Stored as a list of agent name strings. An empty list means all agents' messages will show
the Trello Export button. Legacy documents may have `integrations.export_agent` (a single
string) instead — `_normalize_export_agents()` in `services.py` migrates that on read
without requiring a DB migration script.

Each future integration stores its own allowlist. For Jira, allowlists are **per type**:
`integrations.jira.software.export_agents`,
`integrations.jira.service_desk.export_agents`,
`integrations.jira.business.export_agents`.
The `integrations` root never holds an `export_agent` field in new documents.

---

## Validation Constraints

| Field | Type | Constraint |
|-------|------|------------|
| `project_name` | str | non-empty, unique in collection |
| `objective` | str | any string (may be empty) |
| `agents[].name` | str | valid Python identifier after sanitization |
| `agents[].model` | str | must be in `agent_models.json` |
| `agents[].system_prompt` | str | non-empty |
| `agents[].temperature` | float | 0.0 ≤ value ≤ 2.0 |
| `human_gate.name` | str | required when `enabled=true` |
| `human_gate.quorum` | str | `"yes"` \| `"first_win"` \| `"team_config"`; reset to `"yes"` when disabled |
| `human_gate.remote_users[].name` | str | non-empty, unique within list |
| `human_gate.remote_users[].id` | str | server-minted UUID, preserved across saves |
| `human_gate.remote_users` | list | reset to `[]` when `enabled=false`; rejected when `len(agents)==1`; per-user enable/disable is runtime-only (not stored) |
| `team.type` | str | `"round_robin"` or `"selector"` |
| `team.max_iterations` | int | ≥ 1; ≤ 10 when `human_gate.enabled=false` |
| `single-assistant rule` | logical | if `len(agents)==1`, then `human_gate.enabled=true` and `team.type != "selector"` |
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
