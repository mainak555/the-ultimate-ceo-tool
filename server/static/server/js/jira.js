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

  var SCHEMAS = {
    software:     ["summary", "description", "issue_type", "priority", "sprint", "epic", "labels", "story_points", "components", "acceptance_criteria", "confidence_score"],
    service_desk: ["summary", "description", "request_type", "priority", "labels", "impact", "urgency", "confidence_score"],
    business:     ["summary", "description", "issue_type", "priority", "labels", "due_date", "category", "confidence_score"],
  };

  var FIELD_LABELS = {
    summary:              "Summary",
    description:          "Description",
    issue_type:           "Issue Type",
    request_type:         "Request Type",
    priority:             "Priority",
    sprint:               "Sprint",
    epic:                 "Epic",
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
      issue_type: ["Story", "Bug", "Task", "Epic", "Subtask"],
      priority: ["Highest", "High", "Medium", "Low", "Lowest"],
      sprint: [{ value: "", label: "Backlog" }],
      epic: [{ value: "", label: "None" }],
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

  function emptyIssue(typeName) {
    var issue = {};
    (SCHEMAS[typeName] || SCHEMAS.software).forEach(function (f) {
      if (_isArray(f))               { issue[f] = []; }
      else if (f === "confidence_score") { issue[f] = 0.0; }
      else if (f === "story_points")     { issue[f] = null; }
      else                               { issue[f] = ""; }
    });
    return issue;
  }

  // ---------------------------------------------------------------------------
  // Render issue editor cards into a container element
  // ---------------------------------------------------------------------------

  function renderEditorIssues(containerId, countId, issues, typeName, isExported, fieldOptions) {
    var editorEl = document.getElementById(containerId);
    if (!editorEl) return;

    var schema = SCHEMAS[typeName] || SCHEMAS.software;
    var html   = "";

    (issues || []).forEach(function (issue, idx) {
      html += '<div class="jira-issue-card" data-issue-index="' + idx + '">';
      html += '<div class="jira-issue-card__header">';
      html += '<span class="jira-issue-card__title">Issue ' + (idx + 1) + '</span>';
      if (!isExported) {
        html += '<button type="button" class="btn btn--sm btn--danger js-delete-issue"'
              + ' data-issue-index="' + idx + '">&times;</button>';
      }
      html += '</div>';

      schema.forEach(function (field) {
        var label    = FIELD_LABELS[field] || field;
        var value    = issue[field];
        var disabled = isExported ? " disabled" : "";

        html += '<div class="jira-issue-card__field">';
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
      });

      html += '</div>'; // .jira-issue-card
    });

    editorEl.innerHTML = html;

    // Update count badge
    var countEl = document.getElementById(countId);
    if (countEl) countEl.textContent = (issues || []).length;
  }

  // ---------------------------------------------------------------------------
  // Collect edited issues from a container element
  // ---------------------------------------------------------------------------

  function collectIssuesFromEditor(containerId, typeName) {
    var editorEl = document.getElementById(containerId);
    if (!editorEl) return [];

    var schema = SCHEMAS[typeName] || SCHEMAS.software;
    var cards  = editorEl.querySelectorAll(".jira-issue-card");
    var result = [];

    cards.forEach(function (card) {
      var issue = {};
      schema.forEach(function (field) {
        if (_isArray(field)) {
          var raw = (card.querySelector("[data-field='" + field + "']") || {}).value || "";
          issue[field] = raw.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        } else if (field === "confidence_score") {
          var valEl = card.querySelector("[data-field='confidence_score']");
          issue[field] = valEl ? (parseFloat(valEl.value) || 0.0) : 0.0;
        } else if (field === "story_points") {
          var spEl  = card.querySelector("[data-field='story_points']");
          var spVal = spEl ? spEl.value.trim() : "";
          issue[field] = spVal === "" ? null : (parseInt(spVal, 10) || null);
        } else {
          var el = card.querySelector("[data-field='" + field + "']");
          issue[field] = el ? (el.value || "").trim() : "";
        }
      });
      result.push(issue);
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
    getDefaultFieldOptions:   getDefaultFieldOptions,
    emptyIssue:              emptyIssue,
    renderEditorIssues:      renderEditorIssues,
    collectIssuesFromEditor: collectIssuesFromEditor,
  };

  // Legacy no-op shim — prevents "JiraExport is not defined" errors in any
  // remaining call sites until all references are removed.
  window.JiraExport = { openModal: function () {}, closeModal: function () {} };

})();
