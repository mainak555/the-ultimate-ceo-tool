---
name: pluggable-export-provider
description: Add new export providers via ProviderRegistry and provider-specific endpoints without changing shared orchestration modules.
---

# Skill: Export Provider Adapter

## Purpose
Add a new export provider without modifying shared orchestration logic.

## Registry Contract
Register provider capabilities through window.ProviderRegistry:

- openExportModal(context)
- syncConfigState(context) (optional if provider has no config surface)

## Context Contract
Expected context keys:
- provider
- sessionId
- discussionId
- secretKey
- csrfToken

## Endpoint Contract
Implement provider endpoints following this shape:
1. POST /<provider>/<session_id>/extract/<discussion_id>/
2. GET /<provider>/<session_id>/export/<discussion_id>/
3. POST /<provider>/<session_id>/export/<discussion_id>/
4. GET /<provider>/<session_id>/reference/<discussion_id>/
5. POST /<provider>/<session_id>/push/

## Separation Rules
1. No provider-name switches in home.js or project_config.js.
2. Provider-specific logic stays in provider files (views/service/client/js).
3. Shared modules only call ProviderRegistry.

## Validation Checklist
1. New provider works after registering itself.
2. Shared modules require no provider-specific code edits.
3. Missing provider module fails gracefully (no JS crash).
