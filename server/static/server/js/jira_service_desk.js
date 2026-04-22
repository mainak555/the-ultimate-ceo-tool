/**
 * jira_service_desk.js — Jira Service Desk export adapter.
 *
 * Implements the ExportModalBase adapter interface.
 * Registered as provider "jira_service_desk" in window.ProviderRegistry.
 *
 * Depends on: export_modal_base.js, provider_registry.js, jira.js (JiraUtils)
 */

(function () {
  "use strict";

  var TYPE   = "service_desk";
  var PREFIX = "jira-sd";

  // ---------------------------------------------------------------------------
  // Per-open state (reset in onOpen)
  // ---------------------------------------------------------------------------

  var _state = {
    issues:   [],
    exported: false,
    defaults: {},
    baseAPI:  null,
  };

  // ---------------------------------------------------------------------------
  // Internal shorthand helpers
  // ---------------------------------------------------------------------------

  function _setStatus(msg) {
    if (_state.baseAPI) _state.baseAPI.setStatus(msg);
  }

  function _syncFooter() {
    if (_state.baseAPI) _state.baseAPI.syncFooter();
  }

  function _api(ctx, method, path, body) {
    return window.JiraUtils.api(ctx, method, path, body);
  }

  // ---------------------------------------------------------------------------
  // Render helpers (delegate to JiraUtils)
  // ---------------------------------------------------------------------------

  function _renderIssues(isExported) {
    window.JiraUtils.renderEditorIssues(
      PREFIX + "-editor-issues",
      PREFIX + "-issue-count",
      _state.issues,
      TYPE,
      isExported || false
    );
  }

  // ---------------------------------------------------------------------------
  // API calls
  // ---------------------------------------------------------------------------

  function _checkStatus(ctx) {
    _setStatus("Checking connection\u2026");
    _api(ctx, "GET", "/jira/" + ctx.sessionId + "/token-status/" + TYPE + "/")
      .then(function (d) {
        var el = document.getElementById(PREFIX + "-token-status");
        if (!el) return;
        if (d.valid) {
          el.innerHTML =
            '<span class="export-modal__token-status export-modal__token-status--valid">'
            + window.JiraUtils.jiraTypeName(TYPE) + ' connected</span>'
            + (d.configured_at ? ' <small>(configured ' + window.JiraUtils.esc(d.configured_at) + ')</small>' : '');
          if (d.defaults) _state.defaults = d.defaults;
          _showDestination(ctx);
        } else {
          var err = d.error || "Not connected. Configure Jira Service Desk credentials in Project Settings.";
          el.innerHTML = '<span class="export-modal__token-status export-modal__token-status--error">'
                       + window.JiraUtils.esc(err) + '</span>';
        }
        _setStatus("");
      })
      .catch(function (err) {
        var el = document.getElementById(PREFIX + "-token-status");
        if (el) el.innerHTML = '<span class="export-modal__token-status export-modal__token-status--error">'
                              + window.JiraUtils.esc(err.message) + '</span>';
        _setStatus("");
      });
  }

  function _showDestination(ctx) {
    var section = document.getElementById(PREFIX + "-destination-section");
    if (section) section.hidden = false;
    _loadProjects(ctx);
    _loadSavedExport(ctx);
  }

  function _loadProjects(ctx) {
    var sel = document.getElementById(PREFIX + "-project-select");
    if (!sel) return;
    sel.innerHTML = '<option value="">Loading\u2026</option>';
    _api(ctx, "GET", "/jira/" + ctx.sessionId + "/spaces/" + TYPE + "/")
      .then(function (list) {
        var defaultKey = (_state.defaults && _state.defaults.default_project_key) || "";
        var html = '<option value="">- Select Service Desk -</option>';
        (list || []).forEach(function (p) {
          html += '<option value="' + window.JiraUtils.esc(p.key) + '"'
                + (p.key === defaultKey ? ' selected' : '') + '>'
                + window.JiraUtils.esc(p.name) + ' (' + window.JiraUtils.esc(p.key) + ')'
                + '</option>';
        });
        sel.innerHTML = html;
        _syncFooter();
      })
      .catch(function (err) {
        sel.innerHTML = '<option value="">Error loading service desks</option>';
        _setStatus("Error loading service desks: " + err.message);
      });
  }

  function _loadSavedExport(ctx) {
    if (!ctx.discussionId) return;
    _setStatus("Loading saved export\u2026");
    _api(ctx, "GET", "/jira/" + ctx.sessionId + "/export/" + encodeURIComponent(ctx.discussionId) + "/" + TYPE + "/")
      .then(function (data) {
        var payload = data.export || {};
        var issues  = payload.issues || [];
        _state.issues   = issues.length ? issues : [window.JiraUtils.emptyIssue(TYPE)];
        _state.exported = !!payload.exported;
        _renderIssues(_state.exported);
        if (_state.exported) {
          _setStatus("Already exported to Jira Service Desk. Click Extract Items to unlock editing.");
        } else {
          _setStatus(data.saved ? "Loaded saved export." : "No saved export found. Extract or edit manually.");
        }
        _syncFooter();
      })
      .catch(function () {
        _state.issues   = [window.JiraUtils.emptyIssue(TYPE)];
        _state.exported = false;
        _renderIssues(false);
        _setStatus("No saved export found. Extract or edit manually.");
        _syncFooter();
      });
  }

  // ---------------------------------------------------------------------------
  // Adapter lifecycle callbacks
  // ---------------------------------------------------------------------------

  function _onExtract(ctx) {
    if (!ctx.discussionId) { _setStatus("Cannot extract: no active discussion."); return; }
    _setStatus("Extracting requests\u2026");
    var btn = document.getElementById("export-modal-extract-btn");
    if (btn) btn.disabled = true;

    _api(ctx, "POST", "/jira/" + ctx.sessionId + "/extract/" + encodeURIComponent(ctx.discussionId) + "/" + TYPE + "/")
      .then(function (d) {
        var items    = d.items || [];
        _state.issues   = items.length ? items : [window.JiraUtils.emptyIssue(TYPE)];
        _state.exported = false;
        _renderIssues(false);
        _setStatus("Extracted " + items.length + " request(s). Editing unlocked.");
        if (btn) btn.disabled = false;
        _syncFooter();
      })
      .catch(function (err) {
        _setStatus("Extraction error: " + err.message);
        if (btn) btn.disabled = false;
      });
  }

  function _onSave(ctx) {
    if (_state.exported) { _setStatus("Export is locked. Click Extract Items to unlock editing."); return; }
    _state.issues = window.JiraUtils.collectIssuesFromEditor(PREFIX + "-editor-issues", TYPE);
    _setStatus("Saving\u2026");
    _api(ctx, "POST", "/jira/" + ctx.sessionId + "/export/" + encodeURIComponent(ctx.discussionId) + "/" + TYPE + "/", {
      issues: _state.issues,
      source: "manual",
    })
      .then(function (d) {
        _state.issues   = (d.export && d.export.issues) || _state.issues;
        _state.exported = !!(d.export && d.export.exported);
        _renderIssues(_state.exported);
        _setStatus("Saved export JSON.");
        _syncFooter();
      })
      .catch(function (err) { _setStatus("Save error: " + err.message); });
  }

  function _onPush(ctx) {
    if (_state.exported) { _setStatus("Already exported. Click Extract Items for a new export."); return; }
    var projectKey = (document.getElementById(PREFIX + "-project-select") || {}).value || "";
    _state.issues = window.JiraUtils.collectIssuesFromEditor(PREFIX + "-editor-issues", TYPE);

    if (!projectKey || !_state.issues.length) return;

    _setStatus("Pushing to Jira Service Desk\u2026");
    var btn = document.getElementById("export-modal-push-btn");
    if (btn) btn.disabled = true;

    _api(ctx, "POST", "/jira/" + ctx.sessionId + "/push/" + TYPE + "/", {
      project_key:   projectKey,
      discussion_id: ctx.discussionId,
      issues:        _state.issues,
    })
      .then(function (d) {
        var count       = (d.result || []).length;
        _state.exported = true;
        _renderIssues(true);
        _syncFooter();
        _setStatus("Pushed " + count + " request(s) to Jira Service Desk.");
        if (btn) btn.disabled = false;
      })
      .catch(function (err) {
        _setStatus("Push error: " + err.message);
        if (btn) btn.disabled = false;
      });
  }

  // ---------------------------------------------------------------------------
  // Left-pane event binding
  // ---------------------------------------------------------------------------

  function _bindLeftPaneEvents(ctx) {
    var sel = document.getElementById(PREFIX + "-project-select");
    if (sel) sel.addEventListener("change", _syncFooter);

    var addBtn = document.getElementById(PREFIX + "-add-issue-btn");
    if (addBtn) addBtn.addEventListener("click", function () {
      if (_state.exported) { _setStatus("Export is locked. Click Extract Items to unlock."); return; }
      _state.issues.push(window.JiraUtils.emptyIssue(TYPE));
      _renderIssues(false);
      _syncFooter();
    });

    var editorEl = document.getElementById(PREFIX + "-editor-issues");
    if (editorEl) {
      editorEl.addEventListener("click", function (e) {
        if (_state.exported) { _setStatus("Export is locked. Click Extract Items to unlock."); return; }
        var btn = e.target.closest(".js-delete-issue");
        if (!btn) return;
        var idx = parseInt(btn.getAttribute("data-issue-index") || "-1", 10);
        if (idx < 0) return;
        _state.issues = window.JiraUtils.collectIssuesFromEditor(PREFIX + "-editor-issues", TYPE);
        _state.issues.splice(idx, 1);
        if (!_state.issues.length) _state.issues.push(window.JiraUtils.emptyIssue(TYPE));
        _renderIssues(false);
        _syncFooter();
      });

      editorEl.addEventListener("input", _syncFooter);
    }
  }

  // ---------------------------------------------------------------------------
  // Adapter — left-pane HTML
  // ---------------------------------------------------------------------------

  function _renderLeftPane(ctx) {
    return ''
      + '<div class="export-modal__section" id="' + PREFIX + '-token-section">'
      + '<h4>Connection</h4>'
      + '<div id="' + PREFIX + '-token-status">Checking connection\u2026</div>'
      + '</div>'

      + '<div class="export-modal__section" id="' + PREFIX + '-destination-section" hidden>'
      + '<h4>Destination Service Desk</h4>'
      + '<select id="' + PREFIX + '-project-select" class="input input--sm">'
      + '<option value="">- Select Service Desk -</option>'
      + '</select>'
      + '</div>'

      + '<div class="export-modal__section">'
      + '<div class="export-modal__section-head">'
      + '<h4>Requests <span class="export-modal__count-badge" id="' + PREFIX + '-issue-count">0</span></h4>'
      + '<button type="button" class="btn btn--sm btn--primary" id="' + PREFIX + '-add-issue-btn">Add Request</button>'
      + '</div>'
      + '<div id="' + PREFIX + '-editor-issues" class="jira-editor__issues"></div>'
      + '</div>';
  }

  // ---------------------------------------------------------------------------
  // JiraServiceDeskAdapter — the adapter object
  // ---------------------------------------------------------------------------

  var JiraServiceDeskAdapter = {
    label: "Jira Service Desk",

    referenceUrl: function (ctx) {
      if (!ctx.sessionId || !ctx.discussionId) return null;
      return "/jira/" + ctx.sessionId + "/reference/" + encodeURIComponent(ctx.discussionId) + "/";
    },

    renderLeftPane: function (ctx) {
      return _renderLeftPane(ctx);
    },

    onOpen: function (ctx, baseAPI) {
      _state = {
        issues:   [],
        exported: false,
        defaults: {},
        baseAPI:  baseAPI,
      };
      _bindLeftPaneEvents(ctx);
      _checkStatus(ctx);
    },

    onExtract: function (ctx, baseAPI) { _onExtract(ctx); },
    onSave:    function (ctx, baseAPI) { _onSave(ctx); },
    onPush:    function (ctx, baseAPI) { _onPush(ctx); },

    syncFooter: function (ctx, baseAPI) {
      var hasIssues  = !!(_state.issues && _state.issues.length);
      var isExported = !!_state.exported;
      var projectKey = (document.getElementById(PREFIX + "-project-select") || {}).value || "";

      return {
        extractHidden:   !ctx.discussionId,
        extractDisabled: !ctx.discussionId,
        saveDisabled:    !hasIssues,
        pushHidden:      !hasIssues,
        pushDisabled:    !hasIssues || !projectKey,
      };
    },
  };

  // ---------------------------------------------------------------------------
  // ProviderRegistry registration
  // ---------------------------------------------------------------------------

  function _init() {
    if (!window.ProviderRegistry || !window.ExportModalBase) return;
    window.ProviderRegistry.register("jira_service_desk", {
      openExportModal: function (ctx) {
        window.ExportModalBase.open(ctx, JiraServiceDeskAdapter);
      },
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _init);
  } else {
    _init();
  }
})();
