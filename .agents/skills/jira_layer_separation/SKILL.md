---
name: jira-layer-separation
description: Enforce strict Jira separation of concerns across backend type services, shared facade, and frontend provider wrappers/factory.
---

# Skill: Jira Layer Separation

## Purpose
Keep Jira implementation cleanly separated by type (`software`, `service_desk`, `business`) while preserving a shared facade for common workflows.

## Mandatory Backend Ownership
1. Shared/common Jira logic must stay in `server/jira_service.py`.
2. Software-only logic must stay in `server/jira_software_service.py`.
3. Service Desk-only logic must stay in `server/jira_service_desk_service.py`.
4. Business-only logic must stay in `server/jira_business_service.py`.
5. `server/jira_views.py` must import only the shared `jira_service` facade.

## Backend Separation Rules
1. Type modules must not import `jira_service.py`.
2. Shared helpers (credential resolution, payload persistence, discussion reference reads) stay in `jira_service.py`.
3. Type-specific behaviors (normalization, spaces fetch flavor, push flavor) stay in type modules.
4. Keep endpoint contract unchanged when refactoring layers.

## Mandatory Frontend Ownership
1. Shared Jira export adapter behavior belongs in `server/static/server/js/jira_adapter_factory.js`.
2. Shared Jira helper utilities belong in `server/static/server/js/jira.js` (`window.JiraUtils`).
3. Type registration wrappers belong in:
   - `server/static/server/js/jira_software.js`
   - `server/static/server/js/jira_service_desk.js`
   - `server/static/server/js/jira_business.js`

## Frontend Separation Rules
1. Per-type wrapper files should only construct config and register with `window.ProviderRegistry`.
2. Wrapper files must not duplicate extract/save/push lifecycle logic.
3. Keep `home.js`, `provider_registry.js`, and `export_modal_base.js` provider-agnostic.
4. Ensure script load order in `home.html` is:
   - `jira.js`
   - `jira_adapter_factory.js`
   - type wrapper files

## Validation Checklist
1. All Jira flows still work for each type: verify, spaces, extract, save, push.
2. No circular imports in backend module graph.
3. No duplicated modal lifecycle logic in per-type JS wrappers.
4. Shared modules contain no hardcoded Jira type branches outside Jira-owned modules.
