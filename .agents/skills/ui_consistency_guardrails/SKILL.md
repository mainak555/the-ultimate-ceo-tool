---
name: visual-consistency-standards
description: Enforce consistent delete controls, color tokens, spacing, and typography across pages and export modals.
---

# Skill: UI Consistency Guardrails

## Purpose
Preserve a consistent visual language across Config, Home, and export modals.

## Required Consistency Areas
1. Delete controls: icon style, hover behavior, and danger semantics must match shared patterns.
2. Color scheme: use shared SCSS tokens and existing button modifiers.
3. Card/modal spacing: preserve established padding, border radius, and hierarchy rhythm.
4. Typography: keep existing text scale and label hierarchy unless design system change is intentional.

## Export Modal Guardrails
1. Keep baseline split layout and action order consistent across providers.
2. Provider-specific accents are allowed; baseline control semantics must remain unchanged.
3. Do not create one-off destructive button styles when a shared style already exists.

## Review Checklist
1. Compare delete controls with chat list and agent card patterns.
2. Confirm no hardcoded random colors; use variables.
3. Confirm responsive behavior remains usable on mobile and desktop.
