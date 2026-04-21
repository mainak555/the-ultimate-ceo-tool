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

### Cards And Modals

1. Preserve shared card/modal spacing rhythm from `main.scss`.
2. Keep border, radius, and typography aligned with existing patterns.
3. Provider-specific accents are additive only; baseline control semantics are unchanged.

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
