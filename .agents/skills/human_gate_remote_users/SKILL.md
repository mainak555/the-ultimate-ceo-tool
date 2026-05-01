---
name: human-gate-remote-users
description: Use when adding, changing, or reviewing the Human Gate `remote_users` list, the `quorum` enum, or any UI/validation/runtime that touches multi-user collaboration on top of the existing Human Gate. Enforces single-assistant restriction, UUID round-trip, legacy-bool migration, key/value form pattern reuse, and the leader-vs-remote split.
---

# Human Gate — Remote Users (collaborative)

The single-user Human Gate has been extended into a multi-user collaborative
contract. The local user (the person running this app) is the **session
leader**: they own MCP authorizations and start every run. **Remote users**
only join an active chat session via a per-session join URL and may respond at
the Human Gate.

Phase 1 ships configuration only. Runtime remote-response collection (Redis
hash → N appended `discussions[].role="user"` entries before the next
`team.run_stream`) is delivered in Phase 3.

---

## Schema (Mongo `project_settings.human_gate`)

```python
human_gate = {
    "enabled": bool,
    "name": str,            # required when enabled
    "quorum": str,          # "yes" | "first_win" | "team_config", default "yes"
    "remote_users": [       # default []
        {
            "id": str,          # UUID v4 — server-preserved across saves
            "name": str,        # required, non-empty, unique within list
            "description": str, # plain-language role; used by Selector routing
        }
        # Per-user enable/disable is a *runtime* concern (lobby/readiness in
        # Phase 2) and is NOT a stored config field.
    ],
}
```

### Reset rules (mandatory)

- When `enabled = false`: `name = ""`, `remote_users = []`, `quorum = "yes"`.
- When `len(agents) == 1` (single-assistant): a non-empty `remote_users` list
  is **rejected** with `ValueError`. Selector routing is invalid for one
  assistant; remote collaboration requires a multi-agent team.

### Legacy migration

Older documents may carry `quorum: bool`. Migrate on read in both
`server/services.py::normalize_project()` and
`server/schemas.py::validate_human_gate()`:

- `True  → "yes"`
- `False → "first_win"`

Unknown enum values from API/import paths must raise `ValueError` from
`validate_human_gate`. Unknown values found in stored documents must be
silently coerced to `"yes"` in `normalize_project` (read-side defensive
default).

---

## Form contract (`config_form.html`)

The Remote Users block lives **inside** `#human-gate-fields` so it inherits
the gate-disabled hide behavior, plus its own `id="human-gate-remote-block"`
that is hidden when `len(agents) == 1`.

- `quorum` is a single `<select name="human_gate[quorum]">` with three
  options matching the schema enum.
- Remote user rows follow `.agents/skills/key_value_form_pattern/SKILL.md`:
  - Container: `#remote-users-rows`
  - Row: `<fieldset class="remote-users__row form-group--nested" data-remote-index="N">`
  - Hidden `id` field (preserves UUID round-trip):
    `name="human_gate[remote_users][N][id]"`
  - Inputs: `name="human_gate[remote_users][N][name]"` and `[description]`.
  - Add button: `.js-add-remote-user` in the subsection header.
  - Per-row delete: `.chat-session-item__delete.js-delete-remote-user`.
- New rows mint a fresh UUID via `crypto.randomUUID()` (with an RFC-4122 v4
  fallback) — never leave the hidden `id` blank on a fresh row.
- All textareas in this block must include a `<small class="form-hint">`
  (AGENTS.md rule #22).

JS row-handler responsibilities:

- `reindexRemoteUsers()` rewrites every `name="human_gate[remote_users][N]…"`
  attribute and every `id`/`for` association so the form continues to submit
  contiguous indices after a delete.
- `syncSingleAssistantMode()` toggles `#human-gate-remote-block.hidden` and
  disables every input inside it when the project has exactly one assistant.

---

## View parser (`server/views.py::_parse_remote_users`)

Iterate `human_gate[remote_users][N][id|name|description]` while any of
those three bracket fields is present in `post_data`. Skip rows where
`name` is blank (UI may submit a fresh empty row). Preserve the submitted
`id` (server-minted on prior save).

Pass the parsed list through under `human_gate.remote_users` and read
`human_gate[quorum]` for the enum value (default `"yes"`).

---

## Validation (`server/schemas.py::validate_human_gate`)

1. Coerce / migrate `quorum` (legacy bool → enum) and reject unknown values.
2. Iterate `remote_users`:
   - Skip blank `name` rows silently.
   - Reject duplicate names (case-insensitive).
   - Mint a UUID when `id` is blank (forward-compatible with API import).
3. Apply reset rules when `enabled = false`.
4. In `validate_project()` (after `validate_human_gate`), reject when
   `assistant_count == 1 and human_gate["remote_users"]`.

---

## Readonly view (`config_readonly.html`)

When `human_gate.enabled` and `len(agents) > 1`:

- Render the `quorum` mode in plain English.
- List remote users (`name`, `description`).

Do not render an enabled/disabled badge — per-user enable/disable is a
runtime concern (lobby/readiness), not config state.

---

## Out of scope (Phase 1)

- Per-session join URL minting and `chat_sessions` membership (Phase 2).
- WebSocket / Channels delivery to the remote chat surface (Phase 3).
- Redis-backed remote response collection feeding `discussions[]` (Phase 3).
- Selector prompt enrichment with the remote-user roster (Phase 3).

When implementing those phases, this skill must be updated alongside the new
contract changes.
