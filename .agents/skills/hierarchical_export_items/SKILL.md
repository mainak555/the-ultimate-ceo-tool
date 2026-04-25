---
name: hierarchical-export-items
description: Implement parent/child hierarchical export items with `temp_id` + `parent_temp_id`, recursive accordion rendering, and BFS push that resolves parent links via a `temp_to_key` map.
---

# Skill: Hierarchical Export Items

## Purpose
Provide a single, reusable contract for export providers that need parent / child item relationships (Jira Software issues, future Asana subtasks, future GitHub Project tracking, etc.). All such providers must follow the contract in this skill.

This pattern is the **standard** for any new export feature where items can nest.

---

## Mandatory Data Contract

Every hierarchical item must carry exactly two bookkeeping fields:

| Field            | Type             | Meaning                                                                 |
|------------------|------------------|-------------------------------------------------------------------------|
| `temp_id`        | `string`         | Client-stable unique id. Generated if missing. Used only for parent linking; never sent to the destination service. |
| `parent_temp_id` | `string \| null` | `temp_id` of the parent item, or `null` for roots.                      |

**Forbidden**: storing a `depth_level` (or any other derived structural field). Depth is recomputed from the `parent_temp_id` chain at render time and at push time. Storing depth introduces drift whenever a node moves and bloats the payload.

### Defensive normalization rules
1. If `temp_id` is missing or empty, generate one (e.g. `T<short-hex>`).
2. If `parent_temp_id == temp_id`, treat the item as a root.
3. If `parent_temp_id` references a `temp_id` that is not present in the same batch, treat the item as a root and surface a non-blocking warning at push time.
4. Duplicate `temp_id` values in input must be reassigned (later occurrence gets a new id).

---

## Mandatory Render Contract (frontend)

The editor must render items as a **recursive accordion tree**, not a flat list:

1. Build the tree from the flat array using `temp_id` + `parent_temp_id`.
2. Render each node with this header structure:
   - Caret toggle button (`▸` collapsed / `▾` expanded).
   - Type badge (issue type, request type, etc.).
   - "Item N · {summary preview}" — sequential number across the depth-first walk.
   - Child count badge (only if the node has children) using shared class `export-modal__count-badge`.
   - **Add Child** button (per node).
   - **Delete** button — cascading: deleting a node removes all its descendants. Confirm when descendants exist.
3. Body holds the editable fields plus hidden inputs for `temp_id` and `parent_temp_id`.
4. Children render inside a `.<provider>-issue-card__children` container that uses token-derived left guide border + `padding-left: $space-md`.
5. Persist collapse state per `temp_id` across re-renders so editing does not collapse the tree.
6. The total count badge equals total nodes across all depths.

### Add Child defaults
"Add Child" should pre-fill the new child's primary type field using a sensible default chain. For Jira Software:

- root → `Epic`
- child of `Epic` / `Feature` → `Story`
- child of `Story` → `Task`
- child of `Task` / `Bug` → `Sub-task`
- otherwise → `Task`

The user may override; defaults exist only to reduce friction.

---

## Mandatory Push Contract (backend)

Push must walk the tree **breadth-first from the roots** so that every parent is created before any of its children:

1. Build `children_of[parent_temp_id] = [items]` and `roots = [items where parent_temp_id is null or unknown]`.
2. Maintain `temp_to_key = {}` populated as each create succeeds.
3. For every non-root item, look up `parent_key = temp_to_key.get(parent_temp_id)`.
   - If the parent failed to create, append a warning (`Parent '<id>' was not created; this issue will be created as a root.`) and skip enqueuing the failed branch's descendants.
   - Otherwise pass the parent reference to the destination API in whatever form it requires (e.g. Jira `fields.parent = {"key": parent_key}`).
4. Each result item must echo back its `temp_id` so the client can correlate.
5. Result list order matches the BFS push order (roots first, then breadth-first by depth).

### Existing-item linkage (Jira Software)
When a Jira Software row carries `existing_issue_key`:
1. Do not create a new Jira issue for that row.
2. Update the selected existing Jira issue with the card's edited fields.
3. Still map `temp_to_key[temp_id] = existing_issue_key` before processing descendants so child parent-link resolution remains deterministic.
4. If parent constraints no longer match, the UI should reset that row to `New` and require explicit reselection.

### Service-specific parent fallback (Jira Software example)
Jira Cloud team-managed projects accept `fields.parent` for the full hierarchy (Epic → Story → Sub-task). Company-managed projects may require the legacy Epic Link customfield (`customfield_10014`) for Story → Epic. On a `400` containing `parent` or `customfield`, retry once with the customfield form before recording a hard failure.

---

## Sprint / Backlog Contract (Jira Software-specific, but worth knowing)

When the destination service distinguishes "active sprint" from "backlog":

- The **left-pane Sprint dropdown** must always include `Backlog` as a hardcoded option with `value=""`. This represents "no sprint".
- Per-card sprint selectors default to whatever the global Sprint selector chose; the global selector cascades on change.
- At push time, **skip the sprint-assignment API call entirely if `sprint == ""`**. The newly-created issue lands in Backlog automatically.
- Skip sprint assignment for issue types the destination forbids (Jira: `Epic`, `Sub-task`).
- Sprint assignment failures must be surfaced as per-issue warnings, never as a hard failure that aborts the batch.

---

## Backward Compatibility

- Existing saved exports without `temp_id` / `parent_temp_id` must still load. On load, generate `temp_id` for any item missing one and treat all such items as roots until the user edits the tree.
- The legacy `epic` per-card field for Jira Software is **removed**; parent linkage replaces it.

---

## Validation Checklist
1. Items carry only `temp_id` + `parent_temp_id` (no `depth_level`).
2. Frontend tree builder reassigns duplicate `temp_id`s and treats unknown parents as roots.
3. Render shows caret toggle, type badge, summary preview, child count, Add Child, Delete (cascade).
4. Collapse state survives re-renders.
5. Backend push walks BFS from roots; parent key is always available before child create.
6. Failed-parent branch records a warning and demotes the child to root rather than crashing.
7. Result entries echo `temp_id`.
8. Backlog (`sprint == ""`) skips the sprint Agile API call.
9. Non-sprintable types (`Epic`, `Sub-task`) skip the sprint Agile API call with a warning.
10. SCSS uses tokens only; child indentation uses `$space-md` and `$color-border`.

---

## Reference Implementation
- Backend push: `server/jira_client.py::push_issues_software`
- Backend normalize: `server/jira_software_service.py::normalize_item`
- Frontend tree + render: `server/static/server/js/jira.js` (`buildIssueTree`, `renderEditorIssues`, `collectIssuesFromEditor`)
- Frontend events: `server/static/server/js/jira_adapter_factory.js` (`bindLeftPaneEvents`)
- SCSS: `server/static/server/scss/main.scss` — `.jira-issue-card__caret`, `.jira-issue-card__type-badge`, `.jira-issue-card__children`, `.jira-issue-card--depth-N`, `.jira-issue-card--collapsed`
- Extractor prompt: `examples/prompts/jira_software.md`
