---
name: export-modal-pattern
description: Implement provider export popups with shared split layout, raw reference pane, and standard extract-save-export lifecycle.
---

# Skill: Export Popup Base

## Purpose
Implement a provider export popup with the same baseline UX as Trello.

## Required Layout
1. Modal with split workbench area.
2. Left pane: editable export workspace.
3. Right pane: raw markdown reference from discussion.content.
4. Footer actions: Extract, Save, Export, Cancel.

## Required Behavior
1. On open, load saved payload for provider/discussion.
2. On open, load raw markdown reference independently from source discussion.content.
3. Do not auto-extract on modal open.
4. Extract action replaces editable payload state only.
5. Save action persists edited payload under discussions[].exports.<provider>.
6. Export action uses current edited payload state.

## Data Source Rules
1. Right pane source: discussions[].content only.
2. Saved export source: discussions[].exports.<provider>.
3. Never overwrite discussion.content during save/export.

## Validation Checklist
1. Reopen modal restores edited payload.
2. Extract, Save, and Export can be run independently.
3. Right pane remains stable even when payload changes.
