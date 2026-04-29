---
name: export_agents_sync
description: >
  Use when adding, changing, or reviewing any code that stores, validates, or displays
  the export_agents allowlist for Trello or any Jira type (software, service_desk, business).
  Enforces the project-wide rule: when an assistant agent is renamed or removed, all
  provider export_agents lists that reference the old name must be reset to [] — never raise
  a validation error, never silently preserve a stale name.
---

# Skill: Export Agents Sync

## Purpose

`export_agents` is a per-provider allowlist of assistant agent **names** that controls which
agents show an export button in the chat UI.  Agent names change (rename) or disappear (delete)
when the config form is edited or when a project is cloned and then modified.  Any mismatch
between stored `export_agents` names and the current `agents[]` list must be handled gracefully
by resetting the entire list to `[]`, never by blocking the save with a `ValueError`.

An empty list means "show export button on every agent" — which is the safest fallback.

---

## Rule

> **Any time an assistant agent is renamed or removed, every provider's `export_agents` list
> must be reset to `[]` if it contains one or more names that no longer match the current
> agent roster.**

This applies to:
- `integrations.trello.export_agents`
- `integrations.jira.software.export_agents`
- `integrations.jira.service_desk.export_agents`
- `integrations.jira.business.export_agents`
- Any future provider that stores an `export_agents` list

The reset is "all-or-nothing": if even one name is stale, the whole list is cleared.

---

## Backend Contract (`server/schemas.py`)

### Validation pattern (required)

```python
raw_ea = raw_source.get("export_agents") or []
if isinstance(raw_ea, str):
    raw_ea = [raw_ea] if raw_ea else []
export_agents = [n.strip() for n in raw_ea if isinstance(n, str) and n.strip()]
lower_names = [n.lower() for n in agent_names]
if any(ea.lower() not in lower_names for ea in export_agents):
    export_agents = []   # reset — do NOT raise ValueError
```

- `agent_names` is the list of **already-validated** agent names from the same save payload.
- Never call `raise ValueError(...)` for an `export_agents` mismatch.
- This pattern must be used in every function that validates a provider's `export_agents`:
  - `validate_integrations()` — Trello
  - `validate_jira_type_config()` — each Jira type
  - Any future provider validator

### Adding a new provider

If a new integration has an `export_agents` list, its validator must follow the pattern above.
Do NOT copy-paste the old `raise ValueError` style from git history.

---

## Frontend Contract (`project_config.js`)

### Checkbox-grid IDs (required)

Every provider's export_agents `checkbox-grid` div must have a **stable `id`** so the JS sync
function can find and rebuild it when the agent roster changes.

| Provider            | `id` attribute                    | Input `name`                                       |
|---------------------|-----------------------------------|----------------------------------------------------|
| Trello              | `integrations-export-agents`      | `integrations[trello][export_agents]`              |
| Jira Software       | `jira-software-export-agents`     | `integrations[jira][software][export_agents]`      |
| Jira Service Desk   | `jira-service-desk-export-agents` | `integrations[jira][service_desk][export_agents]`  |
| Jira Business       | `jira-business-export-agents`     | `integrations[jira][business][export_agents]`      |

Future providers: add a row to the table above AND to the `syncExportAgentCheckboxes()` config
array (see below).

### `syncExportAgentCheckboxes()` pattern (required)

The function must sync every provider in a single loop.  Add an entry to the config array for
each new provider:

```js
[
  { id: "integrations-export-agents",       field: "integrations[trello][export_agents]" },
  { id: "jira-software-export-agents",      field: "integrations[jira][software][export_agents]" },
  { id: "jira-service-desk-export-agents",  field: "integrations[jira][service_desk][export_agents]" },
  { id: "jira-business-export-agents",      field: "integrations[jira][business][export_agents]" },
  // NEW PROVIDER:
  { id: "{provider}-export-agents",         field: "integrations[{provider}][export_agents]" },
].forEach(function (cfg) {
  var wrapper = document.getElementById(cfg.id);
  if (!wrapper) return;
  var checkedValues = new Set();
  wrapper.querySelectorAll("input[name='" + cfg.field + "']:checked").forEach(function (cb) {
    checkedValues.add(cb.value);
  });
  var html = "";
  listAgentNames().forEach(function (name) {
    var checked = checkedValues.has(name) ? " checked" : "";
    html += '<div class="form-group form-group--inline"><label>';
    html += '<input type="checkbox" name="' + cfg.field + '" value="' + name + '"' + checked + '>';
    html += ' ' + name + '</label></div>';
  });
  wrapper.innerHTML = html;
});
```

`syncExportAgentCheckboxes()` is called by `syncFormState()` on every agent add/remove/rename,
so the checkboxes stay in sync with the live agent roster without a page reload.

---

## Template Contract (`config_form.html`)

Each provider's export_agents section must follow this structure:

```html
<div class="form-group">
  <label>Export Agents</label>
  <div class="checkbox-grid" id="{provider}-export-agents">
    {% for agent in project.agents %}
      <label>
        <input type="checkbox"
               name="integrations[{provider}][export_agents]"
               value="{{ agent.name }}"
               {% if agent.name in project.integrations.{provider}.export_agents %}checked{% endif %}>
        {{ agent.name }}
      </label>
    {% endfor %}
  </div>
  <small class="form-hint">Checked agents show a {Provider} export button. Leave all unchecked to show on every message.</small>
</div>
```

Key requirements:
- The `checkbox-grid` div **must** have the `id` listed in the table above.
- The `<small class="form-hint">` is mandatory (rule 22 in AGENTS.md).

---

## Adding a New Export Provider — Checklist

1. **Template** — add the `checkbox-grid` with the correct `id` (see table above).
2. **Backend validator** — apply the `if any(...) → []` pattern (never `raise ValueError`).
3. **Frontend sync** — add an entry to the config array in `syncExportAgentCheckboxes()`.
4. **Skill table** — add a row to the ID/field table in this SKILL.md file.
5. **AGENTS.md** — the existing rule already covers new providers; no new rule needed unless
   the reset semantics change.

---

## Background: Why Reset to `[]`?

- Raising a `ValueError` blocks the save entirely, which is the wrong UX when an agent was
  renamed legitimately (the user just wants to save the rename).
- "Filter valid" (keep only the matching subset) is risky when a user renames two agents at
  once — the retained names might not be what the user intended.
- Resetting to `[]` falls back to "show export on all agents", which is the safest and most
  visible state — the user will notice and re-configure if they want a restricted list.
- The JS sync on the frontend already clears stale checkboxes live, so by the time the user
  hits Save, stale names have already been removed from the form — the backend reset is a
  safety net for direct-API callers and clone paths.
