---
name: scss-style-consistency
description: Enforce token-only SCSS, shared component semantics, and consistent visual rhythm across all features and export modals.
---

# Skill: SCSS Style Consistency

## Purpose
Preserve a single visual language across Config, Home, and provider export modals.

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

## Export Modal Guardrails
1. Keep split layout and footer action order consistent with baseline export modal pattern.
2. Provider-specific accents are additive only; do not alter shared destructive/control semantics.
3. Preserve clear visual separation between editable workspace and raw reference pane.

## Review Checklist
1. Confirm token usage only (no hardcoded hex/rgb/hsl).
2. Confirm spacing and radius values use shared tokens.
3. Compare delete controls with existing chat list and agent card patterns.
4. Confirm desktop/mobile usability remains intact.
5. Confirm feature-specific styles are scoped and do not pollute shared layers.
6. Confirm nested sections have symmetric `padding-left` + `padding-right` (both required — right side prevents scrollbar/control clipping).
