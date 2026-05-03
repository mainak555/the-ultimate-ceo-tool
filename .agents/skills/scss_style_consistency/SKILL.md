---
name: scss-style-consistency
description: Enforce token-only SCSS, shared component semantics, and consistent visual rhythm across all features and export modals.
---

# Skill: SCSS Style Consistency

## Purpose
Preserve a single visual language across Config, Home, and provider export modals.

## Scope Boundary
This skill owns token-level implementation (how styles are applied: token usage, spacing rhythm, selector scoping).
Behavior/layout semantics (what controls and interaction order are required) are owned by `.agents/skills/ui_consistency_guardrails/SKILL.md`.

## Required Rules
1. Colors must come from `$color-*` tokens in `server/static/server/scss/_variables.scss`.
2. Spacing must use `$space-*` tokens only.
3. Typography must use shared font family and `$font-size-*` scale.
4. Border radius must use `$radius` or `$radius-lg` only.
5. No hardcoded random colors or one-off destructive styles.

## Shared Pattern Requirements
1. Buttons must use shared `.btn` modifiers for semantic actions.
2. Form controls must use shared `.input` and form helper classes.
3. Card and modal spacing/border rhythm must match existing `main.scss` patterns.
4. Delete controls must match shared icon/button danger behavior.
5. Nested integration sub-sections (`.form-group--nested`, `.form-group--nested-l2`) must always declare both `padding-left` AND `padding-right`. Missing `padding-right` causes textarea scrollbars and inputs to overflow into the parent section's right border.
6. **Readonly card float layout**: `agent-card--readonly` cards must use `overflow: hidden` (clearfix). `agent-card__header-meta` is `float: right` with `text-align: right`, `flex-direction: column`, `align-items: flex-end`, `gap: $space-xs`, `margin-left: $space-md`, `margin-bottom: $space-xs`. `agent-card__title` uses `font-size: $font-size-lg`, `font-weight: 700`. `agent-card__temp` uses `font-size: $font-size-sm`, `color: $color-text-muted`, `font-style: italic`. Do **not** use `justify-content: space-between` on readonly card rows — it creates blank whitespace at all viewport sizes. See `docs/scss_style_guide.md` §"Readonly Card Layout".

## Export Modal Guardrails
1. Keep split layout and footer action order consistent with baseline export modal pattern.
2. Provider-specific accents are additive only; do not alter shared destructive/control semantics.
3. Preserve clear visual separation between editable workspace and raw reference pane.
4. Add/create controls in export modal left panes should use shared class `export-modal__context-add-btn` for consistent contextual button color.
5. Editable item cards in export modals should use token-derived light panel backgrounds (for example `lighten($color-bg, 1.5%)`) with shared border/radius rhythm.
6. Export popup item headings should use the shared count badge class (`export-modal__count-badge`) rather than provider-specific badge styles.
7. Jira Software editor classes (`.jira-editor__issues`, `.jira-issue-card*`, `.jira-workspace-section`) must preserve Trello-like spacing rhythm and token usage.
8. Jira Software issue list scrolling must be implemented on the editor list container (`overflow-y: auto`) with `flex: 1` and `min-height: 0` to avoid clipping and non-scroll states.

## Review Checklist
1. Confirm token usage only (no hardcoded hex/rgb/hsl).
2. Confirm spacing and radius values use shared tokens.
3. Compare delete controls with existing chat list and agent card patterns.
4. Confirm desktop/mobile usability remains intact.
5. Confirm feature-specific styles are scoped and do not pollute shared layers.
6. Confirm nested sections have symmetric `padding-left` + `padding-right` (both required — right side prevents scrollbar/control clipping).
7. Confirm Jira Software destination cascade and issue-card styles remain scoped to Jira classes and do not modify Trello-specific selectors.
8. Confirm export-popup add/create buttons use `export-modal__context-add-btn` and keep consistent hover/focus color behavior.
9. Confirm export-popup editable card backgrounds remain token-derived and visually consistent across providers.
10. Confirm export-popup item heading badges use `export-modal__count-badge` and avoid ad-hoc badge class duplication.
11. Confirm new or updated `agent-card--readonly` cards follow the float layout: `overflow: hidden` on card, `agent-card__header-meta` is `float: right` and first in DOM, `agent-card__title` uses `$font-size-lg`, `agent-card__temp` uses `$color-text-muted` italic. No `justify-content: space-between` on the row.
