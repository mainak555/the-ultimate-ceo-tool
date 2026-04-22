---
name: export-modal-pattern
description: Implement provider export popups with shared split layout, raw reference pane, and standard extract-save-export lifecycle via ExportModalBase.
---

# Skill: Export Popup Base

## Purpose
All export popups share a single modal shell (`window.ExportModalBase`) and implement a
provider-specific **adapter** object. Never build a modal DOM structure inside a provider file.

## Shared Shell
`server/static/server/js/export_modal_base.js` — `window.ExportModalBase`

Entry point: `window.ExportModalBase.open(ctx, adapter)`

The base builds:
- Modal overlay with split 70 / 30 workbench layout
- Header: `Export to {adapter.label}`
- Left pane (70 %): injects `adapter.renderLeftPane(ctx)` HTML
- Right pane (30 %): fetches markdown from `adapter.referenceUrl(ctx)`, renders via `window.MarkdownViewer`
- Footer: Extract Items, Save, Export to {label}, Close — wired to adapter lifecycle hooks
- Right pane heading: initially `Assistant ({label}) Output`; updates to `Assistant ({agent_name}) Output` once the reference fetch resolves

Base-owned element IDs (never redeclare in adapter HTML):
- `#export-modal-overlay`
- `#export-modal-extract-btn`
- `#export-modal-save-btn`
- `#export-modal-push-btn`
- `#export-modal-cancel-btn`
- `#export-modal-reference-title`
- `#export-modal-reference-markdown`
- `#export-modal-status`

## Adapter Interface (every provider must implement)
```js
{
  label,                          // String — e.g. "Trello", "Jira Software"
  renderLeftPane(ctx),            // () => HTML string for left editor pane
  referenceUrl(ctx),              // () => URL string or null
  onOpen(ctx, baseAPI),          // called after DOM is ready
  onExtract(ctx, baseAPI),       // Extract Items button
  onSave(ctx, baseAPI),          // Save button
  onPush(ctx, baseAPI),          // Export button
  syncFooter(ctx, baseAPI),      // returns footer state object (see below)
}
```

## baseAPI Contract (provided by the base to adapter callbacks)
```js
{
  setStatus(msg),   // write to #export-modal-status
  syncFooter(),     // calls adapter.syncFooter() and applies result to footer DOM
  close(),          // removes overlay, clears state
}
```

## syncFooter Return Shape
```js
{
  extractHidden:   bool,
  extractDisabled: bool,
  saveDisabled:    bool,
  pushHidden:      bool,
  pushDisabled:    bool,
}
```
Adapters may also directly manipulate their own left-pane elements (e.g. disable Add Card
button) inside `syncFooter`.

## Context Object
```js
{ provider, sessionId, discussionId, secretKey, csrfToken, projectId }
```
`projectId` is required. Missing it is a defect.

## Required Behavior
1. On open, base loads right-pane reference markdown from `referenceUrl` automatically.
2. On open, adapter's `onOpen` loads saved payload and checks connection status.
3. Do not auto-extract on modal open.
4. Extract action replaces editable payload state only.
5. Save action persists edited payload under `discussions[].exports.<provider>`.
6. Export action uses current edited payload state.

## Data Source Rules
1. Right pane source: `discussions[].content` only — fetched by base via `referenceUrl`.
   - The reference endpoint **must** return `{ markdown, agent_name, discussion_id }`.
   - The base reads `agent_name` from this response and updates `#export-modal-reference-title`.
2. Saved export source: `discussions[].exports.<provider>`.
3. Never overwrite `discussion.content` during save/export.

## Validation Checklist
1. Title reads "Export to {label}" for every provider.
2. Reopen modal restores edited payload.
3. Extract, Save, and Export can be run independently.
4. Right pane remains stable when payload changes.
5. Adapter never builds a modal overlay DOM; only provides `renderLeftPane` HTML string.
6. All adapter element IDs are namespaced (e.g. `trello-*`, `jira-sw-*`) to avoid collisions.
