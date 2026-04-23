---
name: pluggable-export-provider
description: Add new export providers via ProviderRegistry and ExportModalBase adapter pattern without changing shared orchestration modules.
---

# Skill: Export Provider Adapter

## Purpose
Add a new export provider without modifying `home.js`, `app.js`, `export_modal_base.js`,
or `provider_registry.js`. Every provider is a self-contained adapter file.

## Step-by-step Checklist
1. Create `server/static/server/js/{provider}.js` implementing the adapter interface.
2. Add a `<script>` tag AFTER `export_modal_base.js` and `jira.js` (if sharing JiraUtils) in `home.html`.
3. Register with ProviderRegistry at the bottom of the adapter file:
   ```js
   window.ProviderRegistry.register("{provider_name}", {
     openExportModal: function (ctx) {
       window.ExportModalBase.open(ctx, MyAdapter);
     },
   });
   ```
4. Implement all required backend endpoints (see Endpoint Contract below).

## Adapter Interface (all fields required)
```js
{
  label,                        // String — shown in title and Export button
  renderLeftPane(ctx),          // () => HTML string — left pane only (no overlay/header/footer)
  referenceUrl(ctx),            // () => URL or null for the right pane markdown fetch
  onOpen(ctx, baseAPI),        // lifecycle: initialize state, bind left-pane events, load data
  onExtract(ctx, baseAPI),     // lifecycle: run extraction agent
  onSave(ctx, baseAPI),        // lifecycle: save payload to DB
  onPush(ctx, baseAPI),        // lifecycle: push to external service
  syncFooter(ctx, baseAPI),    // () => { extractHidden, extractDisabled, saveDisabled, pushHidden, pushDisabled }
}
```

## Context Contract (always passed as-is from home.js)
```js
{ provider, sessionId, discussionId, secretKey, csrfToken, projectId }
```
`projectId` is **required**. A missing `projectId` is a defect — fix it in `home.js`.

## Element ID Namespacing Rules
- Adapter left-pane IDs must use a provider-specific prefix (e.g. `trello-*`, `jira-sw-*`, `jira-sd-*`, `jira-biz-*`).
- Never redeclare base-owned IDs (`export-modal-*`) inside adapter HTML.

## Jira Types Convention
- Jira registers **three** separate providers: `jira_software`, `jira_service_desk`, `jira_business`.
- All three share utilities from `window.JiraUtils` (defined in `jira.js`).
- Jira shared modal adapter behavior is centralized in `server/static/server/js/jira_adapter_factory.js`.
- Each type keeps its own thin wrapper file (`jira_software.js`, `jira_service_desk.js`, `jira_business.js`) with its own element ID prefix and provider registration.

## Endpoint Contract
Implement provider endpoints following this shape:
1. `POST /<provider>/<session_id>/extract/<discussion_id>/`
2. `GET  /<provider>/<session_id>/export/<discussion_id>/`
3. `POST /<provider>/<session_id>/export/<discussion_id>/`
4. `GET  /<provider>/<session_id>/reference/<discussion_id>/` — must return `{ markdown, agent_name, discussion_id }`; the base uses `agent_name` to update the right-pane heading to `Assistant ({agent_name}) Output`.
5. `POST /<provider>/<session_id>/push/`

## Separation Rules
1. No provider-name switches in `home.js`, `app.js`, or `export_modal_base.js`.
2. Provider-specific logic stays in provider adapter files and backend service/client files.
3. Shared modules call `ProviderRegistry` only.

## Validation Checklist
1. New provider works end-to-end after registering itself.
2. Shared modules required zero edits.
3. Missing provider module fails gracefully — no JS crash in other providers.
4. Title reads "Export to {adapter.label}" automatically.
5. Right pane loads reference markdown without adapter involvement.
