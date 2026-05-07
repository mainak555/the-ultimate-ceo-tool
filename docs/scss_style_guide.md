# SCSS Style Guide

## Purpose

This guide defines the mandatory styling contract for Product Discovery so new UI work stays visually identical across Config, Home, and export modals.

## Source Of Truth

1. Shared tokens live in `server/static/server/scss/_variables.scss`.
2. Shared component patterns live in `server/static/server/scss/main.scss`.
3. New SCSS must follow this guide and `.agents/skills/scss_style_consistency/SKILL.md`.

## Token Rules (Mandatory)

1. Use color tokens only: `$color-*` values from `_variables.scss`.
2. Do not hardcode hex/rgb/hsl colors in component styles.
3. Use spacing tokens only: `$space-xs|sm|md|lg|xl`.
4. Use typography tokens only: `$font-size-sm|base|lg` and `$font-family`.
5. Use radius tokens only: `$radius` and `$radius-lg`.

## Allowed Derivations

1. `lighten()` / `darken()` are allowed only when the input is a token.
2. Derivations must be subtle and local to the feature scope.
3. Do not derive new danger semantics; destructive controls must keep shared behavior.

## Shared Component Contract

### Buttons

1. Use shared classes: `.btn` + modifiers (`.btn--primary`, `.btn--secondary`, `.btn--success`, `.btn--danger`, `.btn--sm`, `.btn--xs`).
2. Do not create one-off destructive styles when `.btn--danger` or shared delete icon styles already exist.
3. Keep disabled semantics consistent (reduced opacity, non-clickable).

### Forms

1. Use shared form classes (`.form-group`, `.form-row`, `.form-actions`, `.form-hint`, `.input`).
2. Preserve label hierarchy (small label size, clear spacing).
3. Keep focus states aligned with shared tokenized input styles.
4. **Every `<textarea>` in config forms must be followed by a `<small class="form-hint">` tag** describing the field's purpose in plain language. The hint must be specific to the field's integration and type (e.g. "Prompt used by the extraction agent to parse the discussion into Jira Software issues."). This applies to all providers (Trello, Jira, future).

### Cards And Modals

1. Preserve shared card/modal spacing rhythm from `main.scss`.
2. Keep border, radius, and typography aligned with existing patterns.
3. Provider-specific accents are additive only; baseline control semantics are unchanged.

### Section Fieldsets (Config Form)

1. All top-level config form sections (Assistant Agents, Human Gate, Team, Integrations) must use the shared `.section-fieldset` class.
2. `.section-fieldset` provides: `border: 1px solid $color-border`, `border-radius: $radius-lg`, `padding: $space-md` (inside `.config-form`).
3. Nested sub-sections within Integrations use `.form-group--nested` (left-border indent, L1) and `.form-group--nested-l2` (L2) — do not apply `.section-fieldset` to nested elements.
4. **Nested sections must always have symmetric horizontal padding**: `padding-left` and `padding-right` must both be set. Omitting `padding-right` causes controls (especially textarea scrollbars) to clip at the parent section's right inner edge.
   - `.form-group--nested`: `padding-left: $space-md`, `padding-right: $space-sm`
   - `.form-group--nested-l2`: `padding-left: $space-md`, `padding-right: $space-sm`
5. **Nesting levels must use identical `margin-left`**: both `.form-group--nested` (L1) and `.form-group--nested-l2` (L2) use `margin-left: $space-md` — do not increase indent at L2 (e.g. `$space-lg`) as it creates visual misalignment between Trello and Jira sub-type sections.
6. **Do not add `margin-top` to nested fieldsets**: vertical rhythm between a checkbox row and the following nested fieldset, and between consecutive nested fieldsets, comes entirely from the preceding `.form-group`'s `margin-bottom` (resolved by `.config-form fieldset.form-group { margin-bottom: $space-md }`). Adding explicit `margin-top` on nested elements doubles the gap and creates inconsistency.
7. **`form-group--nested` is for fieldset sub-sections only — never for card-style rows**: `.form-group--nested` (and `--nested-l2`) applies only to `<fieldset>` blocks that are indented sub-sections of a parent section (e.g. Jira/Trello integration config). Do NOT apply it to repeating card-style rows (e.g. MCP OAuth config rows, MCP Secrets rows, Trello custom-field rows). Card rows own their own `border`, `border-radius`, `padding`, and `background` via a dedicated BEM block class (e.g. `.mcp-oauth-configs__row`). Mixing `form-group--nested` on a card class adds an unwanted `margin-left` and `border-left: 3px` stripe that makes the card appear indented relative to the section header — a visual defect.

## Project Version Badge Classes

Three lightweight classes display the server-managed project version in muted text alongside the project name. All use token-only values and must not be overridden with hardcoded colors or font sizes.

| Class | Location | Key rules |
|---|---|---|
| `.sidebar__version` | `sidebar.html` — project name row | `display: block`, `font-size: $font-size-sm`, `font-weight: 400`, `color: $color-text-muted`, `margin-top: 0.1rem` |
| `.config-form__version` | `config_form.html` — `<h2>` edit header | `font-size: $font-size-sm`, `font-weight: 400`, `color: $color-text-muted`, `margin-left: $space-xs`, `vertical-align: middle` |
| `.config-readonly__version` | `config_readonly.html` — `<h2>` readonly header | Same as `.config-form__version` (shared selector in `main.scss`) |

Rules:
1. **Always wrapped in `<small>`** — never in a `<span>` or inline element with its own `color` attribute.
2. **Token-only styling** — `$color-text-muted`, `$font-size-sm`, `$space-xs` are the only allowed values.
3. **No user-editable state** — version is read-only; never add input or interactive elements to these classes.

## Readonly Card Layout

All `agent-card--readonly` cards across the Config readonly view must share a single float-based header layout. This applies to: **Assistant Agents, Selector/Team, Trello, Jira Software, Jira Service Desk, Jira Business**.

### DOM Order (Mandatory)

```html
<div class="agent-card agent-card--readonly">
  <!-- 1. Meta block FIRST so browser float positions it at right edge -->
  <div class="agent-card__header-meta">
    <span class="badge">model-name</span>
    <em class="agent-card__temp">Temperature: 0.7</em>   <!-- only when applicable -->
  </div>
  <!-- 2. Title as next sibling — flows left beside the float -->
  <strong class="agent-card__title">Card Title</strong>
  <!-- 3. Detail rows fill left column below title; when float height exhausted they go full-width -->
  <div class="agent-card__detail">...</div>
</div>
```

### SCSS Classes (Mandatory Tokens Only)

| Class | Required properties |
|---|---|
| `.agent-card--readonly` | `overflow: hidden` (float clearfix) |
| `.agent-card__header-meta` | `float: right`, `text-align: right`, `margin-left: $space-md`, `margin-bottom: $space-xs`, flex column, `align-items: flex-end`, `gap: $space-xs` |
| `.agent-card__title` | `font-size: $font-size-lg`, `font-weight: 700`, `line-height: 1.4` |
| `.agent-card__temp` | `font-size: $font-size-sm`, `color: $color-text-muted`, `font-style: italic` |

### Rules

1. **`agent-card__header-meta` must be the first child** — float layout depends on source order.
2. **No `agent-card__header` flex wrapper** on readonly cards — the float replaces the flex row.
3. **Temperature is conditional for integration cards**: only render `agent-card__temp` when a system prompt is set on that integration.
4. **Model badge is conditional**: only render when model is non-empty.
5. **Do not use `justify-content: space-between`** on readonly card headers — it creates empty whitespace between title and meta at all viewport sizes.
6. Adding a new card type to the readonly view must follow this exact DOM and class structure with no variation.

## Feature Scoping Rules

1. Feature-specific SCSS must be scoped to feature blocks (for example Trello-specific selectors stay inside Trello sections).
2. Do not move feature logic into shared selectors unless it is truly cross-feature.
3. Shared layers must remain generic and provider-agnostic.

## Responsive Rules

1. UI must remain usable on desktop and mobile.
2. Avoid fixed widths that break narrow screens.
3. Preserve split-to-stack behavior patterns for export modal panes at mobile breakpoints.

## Export Modal Styling Guardrails

1. Keep the reusable baseline layout and action order unchanged.
2. Keep Extract, Save, Export, Cancel semantics visually consistent with shared button patterns.
3. Right pane remains raw reference; visual theming must not blur edit vs reference separation.

## Pull Request Checklist

1. No hardcoded colors in new SCSS blocks.
2. No ad-hoc spacing/radius values outside tokens.
3. Delete controls match shared danger semantics.
4. Buttons/forms/cards reuse shared classes and modifiers.
5. Feature scope is isolated; shared files stay provider-agnostic.
6. Desktop and mobile visual checks completed.
7. Nested sections (`.form-group--nested`, `.form-group--nested-l2`) have both `padding-left` AND `padding-right` set — missing right padding causes controls to clip at the section edge.
