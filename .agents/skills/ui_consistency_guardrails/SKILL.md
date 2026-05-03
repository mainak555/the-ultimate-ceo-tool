---
name: visual-consistency-standards
description: Enforce consistent delete controls, color tokens, spacing, and typography across pages and export modals.
---

# Skill: UI Consistency Guardrails

## Purpose
Preserve a consistent visual language across Config, Home, and export modals.

## Scope Boundary
This skill owns UX/behavior contracts (what must be present, where controls appear, and interaction/order semantics).
Token-level styling implementation (colors, spacing variables, selector scoping) is owned by `.agents/skills/scss_style_consistency/SKILL.md`.

## Required Consistency Areas
1. Delete controls: icon style, hover behavior, and danger semantics must match shared patterns.
2. Color scheme: use shared SCSS tokens and existing button modifiers.
3. Card/modal spacing: preserve established padding, border radius, and hierarchy rhythm.
4. Typography: keep existing text scale and label hierarchy unless design system change is intentional.
5. **Textarea hint text is mandatory**: every `<textarea>` in a config form must be followed by `<small class="form-hint">` with a plain-language description of the field's purpose. The hint must name the integration and type (e.g. "Prompt used by the extraction agent to parse the discussion into Jira Service Desk requests."). No textarea may be left without a hint.
6. **Human gate control order is fixed**: top row = optional decision shortcuts (`Approve`, `Reject`), middle = optional notes textarea (shortcut click prefill uses `APPROVED`/`REJECTED` + blank line), bottom row = execution (`Continue`, `Stop`). Do not reintroduce interaction-mode branching.
7. **Single-assistant config contract is fixed**: when assistant count is exactly 1, the config UI must hide Team Setup and force Human Gate enabled (chat mode). When assistant count is 2+, Team Setup is visible again and normal team controls apply.
8. **Readonly card header layout is fixed for all `agent-card--readonly` cards**: `agent-card__header-meta` (float right) must be the first DOM child; `agent-card__title` follows as a sibling. Do not use `agent-card__header` flex wrapper on readonly cards. The card element requires `overflow: hidden`. See `docs/scss_style_guide.md` §"Readonly Card Layout" for the mandatory DOM structure and the complete list of card types that must comply.

## Export Modal Guardrails
1. Keep baseline split layout and action order consistent across providers.
2. Provider-specific accents are allowed; baseline control semantics must remain unchanged.
3. Do not create one-off destructive button styles when a shared style already exists.
4. Add/create actions for cards/issues/items should use shared class `export-modal__context-add-btn` so contextual button color stays consistent across providers.
5. Editable item cards should share the same light panel background family and border/radius rhythm across providers.
6. Item section headings should show a shared count badge pattern (`Cards <count>`, `Issues <count>`) using `export-modal__count-badge`.
7. Jira Software issue editor must keep delete control in the issue-card header row next to the issue title.
8. Jira Software Add Issue button must stay in the section header row (same row as Issues title/count) and use shared button semantics.
9. Jira Software destination cascade (Project + Sprint) must remain aligned and responsive using shared form/select patterns.

## Review Checklist
1. Compare delete controls with chat list and agent card patterns.
2. Confirm no hardcoded random colors; use variables.
3. Confirm responsive behavior remains usable on mobile and desktop.
4. Confirm every `<textarea>` in a config form has a `<small class="form-hint">` below it with a field-specific description.
5. Confirm Jira Software issue editor list is scrollable within modal workspace and does not force modal-body overflow.
6. Confirm add/create buttons in export popups use the shared contextual style and do not diverge per provider.
7. Confirm editable item card backgrounds are visually consistent between Trello/Jira/future providers.
8. Confirm item heading count badges use `export-modal__count-badge` and reflect current rendered item count.
9. Confirm dropdowns in export item editors do not show duplicate labels (for example repeated `Epic`).
10. Confirm config form reflects single-assistant chat mode: Team section hidden, Human Gate forced on, and explanatory hint shown.
11. Confirm every `agent-card--readonly` card uses the float layout: `agent-card__header-meta` first in DOM (float right), then `agent-card__title`, then detail rows — no `agent-card__header` flex wrapper. Confirm `overflow: hidden` on the card element.
