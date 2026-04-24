/**
 * jira.js — Shared Jira utilities used by all three Jira adapter modules.
 *
 * Exposes: window.JiraUtils
 *
 * Adapter files (jira_software.js, jira_service_desk.js, jira_business.js)
 * use these utilities instead of duplicating common logic.
 *
 * This file does NOT register any ProviderRegistry entries and does NOT open
 * any modal. It is safe to load on both home.html and config.html.
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // HTTP helper
  // ---------------------------------------------------------------------------

  function api(ctx, method, path, body) {
    var headers = {
      "X-App-Secret-Key": (ctx && ctx.secretKey)  || "",
      "X-CSRFToken":      (ctx && ctx.csrfToken)   || "",
      "Content-Type":     "application/json",
    };
    var opts = { method: method, headers: headers };
    if (body) opts.body = JSON.stringify(body);
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.error || "Request failed");
        return d;
      });
    });
  }

  // ---------------------------------------------------------------------------
  // HTML escape
  // ---------------------------------------------------------------------------

  function esc(s) {
    var div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
  }

  // ---------------------------------------------------------------------------
  // Type display names
  // ---------------------------------------------------------------------------

  function jiraTypeName(typeName) {
    var labels = {
      software:     "Jira Software",
      service_desk: "Jira Service Desk",
      business:     "Jira Business",
    };
    return labels[typeName] || typeName;
  }

  // ---------------------------------------------------------------------------
  // Issue field schemas (per type)
  // ---------------------------------------------------------------------------

  // NOTE on hierarchy fields (software):
  //   `temp_id` and `parent_temp_id` are NOT shown as user-editable card
  //   fields — they are persisted as hidden inputs and used to reconstruct
  //   the parent/child tree at render and push time.
  //   `depth_level` is intentionally NOT stored — depth is derived from
  //   `parent_temp_id` chain. See .agents/skills/hierarchical_export_items.
  var SCHEMAS = {
    software:     ["summary", "description", "issue_type", "priority", "sprint", "labels", "story_points", "components", "acceptance_criteria", "confidence_score", "temp_id", "parent_temp_id"],
    service_desk: ["summary", "description", "request_type", "priority", "labels", "impact", "urgency", "confidence_score"],
    business:     ["summary", "description", "issue_type", "priority", "labels", "due_date", "category", "confidence_score"],
  };

  // Hidden hierarchy bookkeeping fields — never rendered as user-facing
  // card fields, but always persisted via hidden inputs for software issues.
  var HIDDEN_FIELDS = ["temp_id", "parent_temp_id"];
  function _isHidden(field) { return HIDDEN_FIELDS.indexOf(field) !== -1; }

  var FIELD_LABELS = {
    summary:              "Summary",
    description:          "Description",
    issue_type:           "Issue Type",
    request_type:         "Request Type",
    priority:             "Priority",
    sprint:               "Sprint",
    labels:               "Labels",
    story_points:         "Story Points",
    components:           "Components",
    acceptance_criteria:  "Acceptance Criteria",
    confidence_score:     "Confidence Score",
    impact:               "Impact",
    urgency:              "Urgency",
    due_date:             "Due Date",
    category:             "Category",
  };

  var TEXTAREA_FIELDS = ["description", "acceptance_criteria", "impact"];
  var ARRAY_FIELDS    = ["labels", "components"];

  var DEFAULT_SELECT_OPTIONS = {
    software: {
      issue_type: ["Epic", "Feature", "Story", "Task", "Sub-task", "Bug"],
      priority: ["Highest", "High", "Medium", "Low", "Lowest"],
      sprint: [{ value: "", label: "Backlog" }],
    },
    service_desk: {
      request_type: ["Service Request", "Incident", "Problem", "Change"],
      priority: ["Highest", "High", "Medium", "Low", "Lowest"],
    },
    business: {
      issue_type: ["Task", "Milestone", "Sub-task", "Epic"],
      priority: ["Highest", "High", "Medium", "Low", "Lowest"],
    },
  };

  function _isTextarea(field) { return TEXTAREA_FIELDS.indexOf(field) !== -1; }
  function _isArray(field)    { return ARRAY_FIELDS.indexOf(field) !== -1; }

  function _normalizeSelectOptions(raw) {
    if (!Array.isArray(raw)) return [];
    return raw
      .map(function (item) {
        if (item && typeof item === "object") {
          var value = String(item.value || item.id || item.key || item.name || "");
          var label = String(item.label || item.name || item.key || item.value || value);
          if (!label && !value) return null;
          return { value: value, label: label || value };
        }
        var text = String(item || "");
        return { value: text, label: text };
      })
      .filter(Boolean);
  }

  function getDefaultFieldOptions(typeName) {
    var src = DEFAULT_SELECT_OPTIONS[typeName] || {};
    var out = {};
    Object.keys(src).forEach(function (field) {
      out[field] = _normalizeSelectOptions(src[field]);
    });
    return out;
  }

  // ---------------------------------------------------------------------------
  // Empty issue factory
  // ---------------------------------------------------------------------------

  var _tempIdCounter = 0;
  function _genTempId() {
    _tempIdCounter += 1;
    return "T" + Date.now().toString(36) + "_" + _tempIdCounter.toString(36);
  }

  function emptyIssue(typeName) {
    var issue = {};
    (SCHEMAS[typeName] || SCHEMAS.software).forEach(function (f) {
      if (_isArray(f))                     { issue[f] = []; }
      else if (f === "confidence_score")   { issue[f] = 0.0; }
      else if (f === "story_points")       { issue[f] = null; }
      else if (f === "temp_id")            { issue[f] = _genTempId(); }
      else if (f === "parent_temp_id")     { issue[f] = null; }
      else                                 { issue[f] = ""; }
    });
    return issue;
  }

  // ---------------------------------------------------------------------------
  // Hierarchical tree helpers (software)
  // ---------------------------------------------------------------------------
  //
  // Tree contract: items carry `temp_id` + `parent_temp_id` (no stored depth).
  // Render and push BOTH derive depth dynamically. Keep this contract in sync
  // with .agents/skills/hierarchical_export_items/SKILL.md.

  // Persistent collapse state (keyed by temp_id) so re-renders preserve UX.
  var _collapsedState = {};

  function setCardCollapsed(tempId, collapsed) {
    if (!tempId) return;
    if (collapsed) _collapsedState[tempId] = true;
    else delete _collapsedState[tempId];
  }
  function isCardCollapsed(tempId) {
    return !!_collapsedState[tempId];
  }

  function buildIssueTree(issues) {
    var byId = {};
    var ordered = [];
    (issues || []).forEach(function (it) {
      if (!it || typeof it !== "object") return;
      if (!it.temp_id) it.temp_id = _genTempId();
      // Defend against duplicate temp_ids by reassigning the later occurrence.
      if (byId[it.temp_id]) it.temp_id = _genTempId();
      byId[it.temp_id] = it;
      ordered.push(it);
    });
    var roots = [];
    var childrenOf = {};
    ordered.forEach(function (it) {
      var pid = it.parent_temp_id || null;
      if (pid && byId[pid] && pid !== it.temp_id) {
        (childrenOf[pid] = childrenOf[pid] || []).push(it);
      } else {
        if (it.parent_temp_id) it.parent_temp_id = null; // orphan → root
        roots.push(it);
      }
    });
    return { byId: byId, childrenOf: childrenOf, roots: roots };
  }

  function _renderIssueField(issue, field, fieldOptions, isExported) {
    var label    = FIELD_LABELS[field] || field;
    var value    = issue[field];
    var disabled = isExported ? " disabled" : "";

    var html = '<div class="jira-issue-card__field">';
    html += '<label>' + esc(label) + '</label>';

    if (fieldOptions && Array.isArray(fieldOptions[field]) && fieldOptions[field].length) {
      var options = fieldOptions[field];
      var valueStr = String(value || "");
      html += '<select class="input input--sm" data-field="' + field + '"' + disabled + '>';
      options.forEach(function (opt) {
        var optValue = String((opt && opt.value) || "");
        var optLabel = String((opt && opt.label) || optValue);
        html += '<option value="' + esc(optValue) + '"'
              + (optValue === valueStr ? ' selected' : '')
              + '>' + esc(optLabel) + '</option>';
      });
      if (valueStr && !options.some(function (opt) { return String((opt && opt.value) || "") === valueStr; })) {
        html += '<option value="' + esc(valueStr) + '" selected>' + esc(valueStr) + '</option>';
      }
      html += '</select>';
    } else if (_isTextarea(field)) {
      html += '<textarea class="input input--sm input--textarea" data-field="' + field + '"'
            + ' rows="3"' + disabled + '>'
            + esc(String(value || "")) + '</textarea>';
    } else if (_isArray(field)) {
      var csvVal = Array.isArray(value) ? value.join(", ") : (value || "");
      html += '<input type="text" class="input input--sm" data-field="' + field + '"'
            + ' value="' + esc(csvVal) + '"' + disabled + ' placeholder="comma-separated">';
    } else if (field === "confidence_score") {
      html += '<input type="number" class="input input--sm" data-field="' + field + '"'
            + ' value="' + (value || 0) + '"' + disabled + ' step="0.05" min="0" max="1">';
    } else if (field === "story_points") {
      var spVal = (value !== null && value !== undefined) ? value : "";
      html += '<input type="number" class="input input--sm" data-field="' + field + '"'
            + ' value="' + spVal + '"' + disabled + ' placeholder="e.g. 5">';
    } else {
      html += '<input type="text" class="input input--sm" data-field="' + field + '"'
            + ' value="' + esc(String(value || "")) + '"' + disabled + '>';
    }

    html += '</div>';
    return html;
  }

  // Recursively render a node + descendants. Returns html string and updates
  // the running counter object (for "Issue N" sequential numbering across the
  // tree, depth-first).
  function _renderHierNode(node, ctx, counter) {
    var schema       = ctx.schema;
    var fieldOptions = ctx.fieldOptions;
    var isExported   = ctx.isExported;
    var childrenOf   = ctx.childrenOf;
    var depth        = ctx.depth;

    counter.n += 1;
    var idx       = counter.n;
    var tempId    = node.temp_id;
    var parentId  = node.parent_temp_id || "";
    var children  = childrenOf[tempId] || [];
    var depthCap  = depth > 4 ? 4 : depth;
    var collapsed = isCardCollapsed(tempId);

    var typeLabel = String(node.issue_type || "Issue").trim() || "Issue";
    var summary   = String(node.summary || "").trim();
    var preview   = summary ? (" · " + (summary.length > 60 ? summary.slice(0, 60) + "…" : summary)) : "";

    var html = '<div class="jira-issue-card jira-issue-card--depth-' + depthCap
             + (collapsed ? ' jira-issue-card--collapsed' : '')
             + '" data-temp-id="' + esc(tempId) + '"'
             + ' data-parent-temp-id="' + esc(parentId) + '"'
             + ' data-issue-index="' + (idx - 1) + '">';

    // Header
    html += '<div class="jira-issue-card__header">';
    html += '<button type="button" class="jira-issue-card__caret js-toggle-issue"'
          + ' data-temp-id="' + esc(tempId) + '"'
          + ' aria-label="Toggle">' + (collapsed ? "▸" : "▾") + '</button>';
    html += '<span class="jira-issue-card__type-badge">' + esc(typeLabel) + '</span>';
    html += '<span class="jira-issue-card__title">Issue ' + idx + esc(preview) + '</span>';
    if (children.length) {
      html += '<span class="export-modal__count-badge jira-issue-card__child-count">'
            + children.length + '</span>';
    }
    html += '<span class="jira-issue-card__header-spacer"></span>';
    if (!isExported) {
      html += '<button type="button" class="btn btn--sm btn--ghost js-add-child"'
            + ' data-temp-id="' + esc(tempId) + '">+ Child</button>';
      html += '<button type="button" class="btn btn--sm btn--danger js-delete-issue"'
            + ' data-temp-id="' + esc(tempId) + '" aria-label="Delete">&times;</button>';
    }
    html += '</div>';

    // Body — fields
    html += '<div class="jira-issue-card__body">';
    schema.forEach(function (field) {
      if (_isHidden(field)) {
        // Hidden hierarchy inputs (live in body so collectIssuesFromEditor
        // can read them via :scope > .jira-issue-card__body).
        var hv = (field === "parent_temp_id") ? (node[field] || "") : String(node[field] || "");
        html += '<input type="hidden" data-field="' + field + '" value="' + esc(String(hv)) + '">';
        return;
      }
      html += _renderIssueField(node, field, fieldOptions, isExported);
    });
    html += '</div>';

    // Children
    if (children.length) {
      html += '<div class="jira-issue-card__children">';
      var childCtx = Object.assign({}, ctx, { depth: depth + 1 });
      children.forEach(function (child) {
        html += _renderHierNode(child, childCtx, counter);
      });
      html += '</div>';
    }

    html += '</div>'; // .jira-issue-card
    return html;
  }

  // ---------------------------------------------------------------------------
  // Render issue editor cards into a container element
  // ---------------------------------------------------------------------------

  function renderEditorIssues(containerId, countId, issues, typeName, isExported, fieldOptions) {
    var editorEl = document.getElementById(containerId);
    if (!editorEl) return;

    var schema = SCHEMAS[typeName] || SCHEMAS.software;
    var html   = "";

    if (typeName === "software") {
      // Hierarchical accordion (tree) renderer.
      var tree    = buildIssueTree(issues);
      var counter = { n: 0 };
      var ctx     = {
        schema:       schema,
        fieldOptions: fieldOptions || {},
        isExported:   !!isExported,
        childrenOf:   tree.childrenOf,
        depth:        0,
      };
      tree.roots.forEach(function (root) {
        html += _renderHierNode(root, ctx, counter);
      });
      editorEl.innerHTML = html;

      // Total count = all nodes across all depths.
      var countEl = document.getElementById(countId);
      if (countEl) countEl.textContent = Object.keys(tree.byId).length;
      return;
    }

    // Flat renderer (service_desk, business — unchanged contract).
    (issues || []).forEach(function (issue, idx) {
      html += '<div class="jira-issue-card" data-issue-index="' + idx + '">';
      html += '<div class="jira-issue-card__header">';
      html += '<span class="jira-issue-card__title">Issue ' + (idx + 1) + '</span>';
      if (!isExported) {
        html += '<button type="button" class="btn btn--sm btn--danger js-delete-issue"'
              + ' data-issue-index="' + idx + '">&times;</button>';
      }
      html += '</div>';
      html += '<div class="jira-issue-card__body">';
      schema.forEach(function (field) {
        if (_isHidden(field)) return;
        html += _renderIssueField(issue, field, fieldOptions, isExported);
      });
      html += '</div>';
      html += '</div>';
    });

    editorEl.innerHTML = html;

    var countEl2 = document.getElementById(countId);
    if (countEl2) countEl2.textContent = (issues || []).length;
  }

  // ---------------------------------------------------------------------------
  // Collect edited issues from a container element
  // ---------------------------------------------------------------------------

  function _collectFieldsFromCard(card, schema) {
    var issue = {};
    schema.forEach(function (field) {
      // Scope queries to the card's own body so we don't pick up nested
      // children's fields.
      var sel = ":scope > .jira-issue-card__body [data-field='" + field + "']";
      var el  = card.querySelector(sel);

      if (_isArray(field)) {
        var raw = el ? (el.value || "") : "";
        issue[field] = raw.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      } else if (field === "confidence_score") {
        issue[field] = el ? (parseFloat(el.value) || 0.0) : 0.0;
      } else if (field === "story_points") {
        var spVal = el ? el.value.trim() : "";
        issue[field] = spVal === "" ? null : (parseInt(spVal, 10) || null);
      } else if (field === "parent_temp_id") {
        var pv = el ? (el.value || "").trim() : "";
        issue[field] = pv || null;
      } else {
        issue[field] = el ? (el.value || "").trim() : "";
      }
    });
    return issue;
  }

  function collectIssuesFromEditor(containerId, typeName) {
    var editorEl = document.getElementById(containerId);
    if (!editorEl) return [];

    var schema = SCHEMAS[typeName] || SCHEMAS.software;
    var result = [];

    if (typeName === "software") {
      // Depth-first walk: parent before children. We rely on the fact that
      // nested cards live inside `.jira-issue-card__children`, so a flat
      // querySelectorAll already returns parents before their descendants.
      var cards = editorEl.querySelectorAll(".jira-issue-card");
      cards.forEach(function (card) {
        result.push(_collectFieldsFromCard(card, schema));
      });
      return result;
    }

    // Flat collection (service_desk, business).
    var flat = editorEl.querySelectorAll(":scope > .jira-issue-card");
    flat.forEach(function (card) {
      result.push(_collectFieldsFromCard(card, schema));
    });
    return result;
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  window.JiraUtils = {
    api:                     api,
    esc:                     esc,
    jiraTypeName:            jiraTypeName,
    SCHEMAS:                 SCHEMAS,
    FIELD_LABELS:            FIELD_LABELS,
    TEXTAREA_FIELDS:         TEXTAREA_FIELDS,
    ARRAY_FIELDS:            ARRAY_FIELDS,
    HIDDEN_FIELDS:           HIDDEN_FIELDS,
    getDefaultFieldOptions:   getDefaultFieldOptions,
    emptyIssue:              emptyIssue,
    genTempId:               _genTempId,
    buildIssueTree:          buildIssueTree,
    setCardCollapsed:        setCardCollapsed,
    isCardCollapsed:         isCardCollapsed,
    renderEditorIssues:      renderEditorIssues,
    collectIssuesFromEditor: collectIssuesFromEditor,
  };

  // Legacy no-op shim — prevents "JiraExport is not defined" errors in any
  // remaining call sites until all references are removed.
  window.JiraExport = { openModal: function () {}, closeModal: function () {} };

})();
