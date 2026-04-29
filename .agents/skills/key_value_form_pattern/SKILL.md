---
name: key-value-form-pattern
description: Use when building repeating KEY/value/delete rows in HTMX-based config forms (MCP secrets, Trello custom fields, Jira custom fields, future provider extras). Enforces grid layout, password input for sensitive values, server-side mask/restore via SECRET_MASK, JS reindex pattern, and validation hooks.
---

# Key/Value Form Pattern

Reusable contract for any "list of `KEY → value` rows with a delete control" UI
in this app — currently MCP Secrets and Trello Custom Fields. Future providers
(Jira custom fields, n8n inputs, header maps, env overrides) MUST use this same
pattern.

---

## When to use

- A configuration field whose value is a variable-length list of pairs.
- Each pair has: a string **KEY**, a value (string or sensitive secret), and
  an inline **delete (×)** control.
- Add a row with `+ Add` button placed in the subsection header.

If the values are **sensitive** (API keys, tokens, secrets), follow the
"Sensitive values" rules below — they are mandatory.

---

## HTML row contract

Use the canonical 3-column grid: `KEY | value | ×`. Match Trello custom fields
and MCP secrets:

```html
<div class="{feature}__subsection">
  <div class="{feature}__subsection-header">
    <strong>Section Title</strong>
    <button type="button" class="btn btn--secondary btn--sm js-add-{feature}">+ Add</button>
  </div>
  <p class="form-hint form-hint--compact">Plain-language description of what this list does.</p>
  <div class="{feature}__rows" id="{feature}-rows">
    <div class="{feature}__row" data-{feature}-index="0">
      <input type="text"
             class="input input--sm js-{feature}-key"
             name="{feature}[0][key]"
             pattern="^[A-Z][A-Z0-9_]*$"
             autocomplete="off">
      <input type="password"   <!-- "text" if non-sensitive -->
             class="input input--sm js-{feature}-value"
             name="{feature}[0][value]"
             autocomplete="new-password">
      <button type="button"
              class="chat-session-item__delete js-delete-{feature}"
              aria-label="Remove" title="Remove">×</button>
    </div>
  </div>
</div>
```

Form fields use **bracket notation** `name="{feature}[N][key]"` /
`name="{feature}[N][value]"`. The Django view parses these by iterating
`while f"{feature}[{idx}][key]" in post_data`.

---

## SCSS contract (token-only, see `docs/scss_style_guide.md`)

```scss
.{feature} {
  &__subsection {
    margin-top: $space-md;
    padding-top: $space-md;
    border-top: 1px solid $color-border;
  }
  &__subsection-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: $space-xs;
  }
  &__rows {
    display: flex;
    flex-direction: column;
    gap: $space-xs;
  }
  &__row {
    display: grid;
    grid-template-columns: 1fr 2fr auto;   // KEY | value | ×
    gap: $space-xs;
    align-items: center;
  }
}
```

NEVER hardcode color or spacing values. Always derive from tokens.

---

## JS contract (`project_config.js` / provider editor)

1. **Add row** — append HTML at next index, no reindex needed.
2. **Delete row** — remove element, then `reindex{Feature}()` to keep
   `name="{feature}[N][...]"` indices contiguous.
3. **Validation on submit** — block submit and `alert()` errors when:
   - a row has a value but no key,
   - a key fails the regex (sensitive values typically require
     `^[A-Z][A-Z0-9_]*$` UPPER_SNAKE),
   - a key is duplicated,
   - a value is empty.

Reindex pattern (mirrors Trello custom fields):

```js
function reindex{Feature}() {
  var container = document.getElementById("{feature}-rows");
  if (!container) return;
  container.querySelectorAll(".{feature}__row").forEach(function (row, idx) {
    row.setAttribute("data-{feature}-index", idx);
    row.querySelector(".js-{feature}-key").name   = "{feature}[" + idx + "][key]";
    row.querySelector(".js-{feature}-value").name = "{feature}[" + idx + "][value]";
  });
}
```

---

## Sensitive values (MANDATORY when value is a secret)

1. **Edit form**: render `<input type="password" value="{stored_value}">`. The
   stored value rendered into the form is `SECRET_MASK` (`••••••••`) — never
   the real secret.
2. **Readonly view**: do NOT render the value at all. Optionally show a
   `🔒 N secrets configured` count badge listing key names only.
3. **Server-side restore**: in `server/services.py::_restore_masked_secrets()`,
   walk submitted dict and replace any `SECRET_MASK` value with
   `existing[key]` from the DB doc. Keys absent from the submitted dict are
   treated as **deleted by the user** and dropped.
4. **Validation** (`server/schemas.py`): keys must match a strict regex
   (`^[A-Z][A-Z0-9_]*$` for env-style secrets), be unique, and have a
   non-empty value.
5. **Logging & tracing**: NEVER log keys + values together. NEVER include
   values in OpenTelemetry span attributes. When a fingerprint is needed,
   compute it over the **placeholder** form (e.g. `{KEY_NAME}` references)
   so it stays stable across rotation.
6. **Runtime substitution**: if values are referenced from another config via
   `{KEY_NAME}` placeholders, substitute them only at the boundary that
   constructs the live object (e.g. `agents/mcp_tools.py` for MCP). Never in
   `server/`.

---

## Reference implementations

| Feature           | Template                                                        | JS                                              | SCSS prefix     |
|-------------------|-----------------------------------------------------------------|-------------------------------------------------|-----------------|
| MCP Secrets       | `server/templates/server/partials/config_form.html`             | `server/static/server/js/project_config.js`     | `.mcp-secrets`  |
| Trello Custom Fields | Trello popup template (`trello.js` `renderCustomFields`)     | `server/static/server/js/trello.js`             | `.trello-editor__custom-field` |

When adding a new key/value form, follow these contracts to the letter.
