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
3. Type-specific behaviors (normalization, spaces fetch flavor, metadata fetch flavor, push flavor) stay in type modules.
4. Keep endpoint contract unchanged when refactoring layers.

## Jira Software Metadata Ownership
1. Session metadata view route lives in `server/jira_views.py`, but calls only the `jira_service` facade.
2. `server/jira_service.py` owns type dispatch for metadata endpoints (for example `/jira/<session_id>/metadata/<type_name>/`).
3. Software-specific metadata aggregation (issue types, priorities, sprints, epics) lives in `server/jira_software_service.py`.
4. Jira REST calls used by metadata live in `server/jira_client.py`; no view-level REST calls.

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
5. Jira connection-status rendering logic (success/error copy) stays centralized in `jira_adapter_factory.js`; wrapper files provide labels only.
6. Jira option normalization/deduplication for issue-type/priority rendering stays centralized in `jira_adapter_factory.js` and Jira metadata helpers.

## Validation Checklist
1. All Jira flows still work for each type: verify, spaces, extract, save, push.
2. Jira Software metadata flow works: project select -> metadata fetch -> dropdown options update.
3. No circular imports in backend module graph.
4. No duplicated modal lifecycle logic in per-type JS wrappers.
5. Shared modules contain no hardcoded Jira type branches outside Jira-owned modules.
6. Connection row shows `{Jira type label} Connected` on success and a type-scoped error message when credentials/config are missing.
7. Issue Type/Priority dropdown options are deduplicated by display label (no repeated `Epic`).
