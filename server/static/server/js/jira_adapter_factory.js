/**
 * jira_adapter_factory.js — Shared Jira export adapter factory.
 *
 * Exposes: window.JiraAdapterFactory.create(config)
 *
 * This keeps per-type provider files thin while preserving distinct
 * provider registrations and ID namespaces.
 */

(function () {
  "use strict";

  function buildConfig(config) {
    return {
      type: config.type,
      prefix: config.prefix,
      label: config.label,
      itemLabel: config.itemLabel || "Issue",
      itemLabelPlural: config.itemLabelPlural || "Issues",
      destinationLabel: config.destinationLabel || "Project",
      loadingDestinationLabel: config.loadingDestinationLabel || "projects",
      extractedLabelPlural: config.extractedLabelPlural || "issue(s)",
      pushedLabelPlural: config.pushedLabelPlural || "issue(s)",
    };
  }

  function create(config) {
    var cfg = buildConfig(config || {});

    var state = {
      issues: [],
      exported: false,
      defaults: {},
      baseAPI: null,
    };

    function setStatus(message) {
      if (state.baseAPI) state.baseAPI.setStatus(message);
    }

    function syncFooter() {
      if (state.baseAPI) state.baseAPI.syncFooter();
    }

    function api(ctx, method, path, body) {
      return window.JiraUtils.api(ctx, method, path, body);
    }

    function renderIssues(isExported) {
      window.JiraUtils.renderEditorIssues(
        cfg.prefix + "-editor-issues",
        cfg.prefix + "-issue-count",
        state.issues,
        cfg.type,
        isExported || false
      );
    }

    function showDestination(ctx) {
      var section = document.getElementById(cfg.prefix + "-destination-section");
      if (section) section.hidden = false;
      loadProjects(ctx);
      loadSavedExport(ctx);
    }

    function checkStatus(ctx) {
      setStatus("Checking connection...");
      api(ctx, "GET", "/jira/" + ctx.sessionId + "/token-status/" + cfg.type + "/")
        .then(function (d) {
          var el = document.getElementById(cfg.prefix + "-token-status");
          if (!el) return;

          if (d.configured) {
            el.innerHTML =
              '<span class="export-modal__token-status export-modal__token-status--valid">'
              + window.JiraUtils.esc(cfg.label) + ' connected</span>';
            state.defaults = {
              default_project_key: (d.default_project_key || "").trim(),
              default_project_name: (d.default_project_name || "").trim(),
            };
            showDestination(ctx);
          } else {
            var err = cfg.label + " is not configured in Project Settings.";
            el.innerHTML = '<span class="export-modal__token-status export-modal__token-status--error">'
                         + window.JiraUtils.esc(err) + '</span>';
          }
          setStatus("");
        })
        .catch(function (err) {
          var el = document.getElementById(cfg.prefix + "-token-status");
          if (el) {
            el.innerHTML = '<span class="export-modal__token-status export-modal__token-status--error">'
                         + window.JiraUtils.esc(err.message) + '</span>';
          }
          setStatus("");
        });
    }

    function loadProjects(ctx) {
      var sel = document.getElementById(cfg.prefix + "-project-select");
      if (!sel) return;

      sel.innerHTML = '<option value="">Loading...</option>';
      api(ctx, "GET", "/jira/" + ctx.sessionId + "/spaces/" + cfg.type + "/")
        .then(function (list) {
          var defaultKey = (state.defaults && state.defaults.default_project_key) || "";
          var html = '<option value="">- Select ' + window.JiraUtils.esc(cfg.destinationLabel) + ' -</option>';
          (list || []).forEach(function (p) {
            html += '<option value="' + window.JiraUtils.esc(p.key) + '"'
                  + (p.key === defaultKey ? ' selected' : '') + '>'
                  + window.JiraUtils.esc(p.name) + ' (' + window.JiraUtils.esc(p.key) + ')'
                  + '</option>';
          });
          sel.innerHTML = html;
          syncFooter();
        })
        .catch(function (err) {
          sel.innerHTML = '<option value="">Error loading ' + window.JiraUtils.esc(cfg.loadingDestinationLabel) + '</option>';
          setStatus("Error loading " + cfg.loadingDestinationLabel + ": " + err.message);
        });
    }

    function loadSavedExport(ctx) {
      if (!ctx.discussionId) return;
      setStatus("Loading saved export...");
      api(ctx, "GET", "/jira/" + ctx.sessionId + "/export/" + encodeURIComponent(ctx.discussionId) + "/" + cfg.type + "/")
        .then(function (data) {
          var payload = data.export || {};
          var issues = payload.issues || [];
          state.issues = issues.length ? issues : [window.JiraUtils.emptyIssue(cfg.type)];
          state.exported = !!payload.exported;
          renderIssues(state.exported);
          if (state.exported) {
            setStatus("Already exported to " + cfg.label + ". Click Extract Items to unlock editing.");
          } else {
            setStatus(data.saved ? "Loaded saved export." : "No saved export found. Extract or edit manually.");
          }
          syncFooter();
        })
        .catch(function () {
          state.issues = [window.JiraUtils.emptyIssue(cfg.type)];
          state.exported = false;
          renderIssues(false);
          setStatus("No saved export found. Extract or edit manually.");
          syncFooter();
        });
    }

    function onExtract(ctx) {
      if (!ctx.discussionId) {
        setStatus("Cannot extract: no active discussion.");
        return;
      }

      setStatus("Extracting " + cfg.itemLabelPlural.toLowerCase() + "...");
      var btn = document.getElementById("export-modal-extract-btn");
      if (btn) btn.disabled = true;

      api(ctx, "POST", "/jira/" + ctx.sessionId + "/extract/" + encodeURIComponent(ctx.discussionId) + "/" + cfg.type + "/")
        .then(function (d) {
          var items = d.items || [];
          state.issues = items.length ? items : [window.JiraUtils.emptyIssue(cfg.type)];
          state.exported = false;
          renderIssues(false);
          setStatus("Extracted " + items.length + " " + cfg.extractedLabelPlural + ". Editing unlocked.");
          if (btn) btn.disabled = false;
          syncFooter();
        })
        .catch(function (err) {
          setStatus("Extraction error: " + err.message);
          if (btn) btn.disabled = false;
        });
    }

    function onSave(ctx) {
      if (state.exported) {
        setStatus("Export is locked. Click Extract Items to unlock editing.");
        return;
      }

      state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);
      setStatus("Saving...");
      api(ctx, "POST", "/jira/" + ctx.sessionId + "/export/" + encodeURIComponent(ctx.discussionId) + "/" + cfg.type + "/", {
        items: state.issues,
        source: "manual",
      })
        .then(function (d) {
          state.issues = (d.export && d.export.issues) || state.issues;
          state.exported = !!(d.export && d.export.exported);
          renderIssues(state.exported);
          setStatus("Saved export JSON.");
          syncFooter();
        })
        .catch(function (err) {
          setStatus("Save error: " + err.message);
        });
    }

    function onPush(ctx) {
      if (state.exported) {
        setStatus("Already exported. Click Extract Items for a new export.");
        return;
      }

      var projectKey = (document.getElementById(cfg.prefix + "-project-select") || {}).value || "";
      state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);

      if (!projectKey || !state.issues.length) return;

      setStatus("Pushing to " + cfg.label + "...");
      var btn = document.getElementById("export-modal-push-btn");
      if (btn) btn.disabled = true;

      api(ctx, "POST", "/jira/" + ctx.sessionId + "/push/" + cfg.type + "/", {
        project_key: projectKey,
        discussion_id: ctx.discussionId,
        items: state.issues,
      })
        .then(function (d) {
          var count = (d.result || []).length;
          state.exported = true;
          renderIssues(true);
          syncFooter();
          setStatus("Pushed " + count + " " + cfg.pushedLabelPlural + " to " + cfg.label + ".");
          if (btn) btn.disabled = false;
        })
        .catch(function (err) {
          setStatus("Push error: " + err.message);
          if (btn) btn.disabled = false;
        });
    }

    function bindLeftPaneEvents() {
      var sel = document.getElementById(cfg.prefix + "-project-select");
      if (sel) sel.addEventListener("change", syncFooter);

      var addBtn = document.getElementById(cfg.prefix + "-add-issue-btn");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          if (state.exported) {
            setStatus("Export is locked. Click Extract Items to unlock.");
            return;
          }
          state.issues.push(window.JiraUtils.emptyIssue(cfg.type));
          renderIssues(false);
          syncFooter();
        });
      }

      var editorEl = document.getElementById(cfg.prefix + "-editor-issues");
      if (editorEl) {
        editorEl.addEventListener("click", function (e) {
          if (state.exported) {
            setStatus("Export is locked. Click Extract Items to unlock.");
            return;
          }
          var btn = e.target.closest(".js-delete-issue");
          if (!btn) return;

          var idx = parseInt(btn.getAttribute("data-issue-index") || "-1", 10);
          if (idx < 0) return;

          state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);
          state.issues.splice(idx, 1);
          if (!state.issues.length) state.issues.push(window.JiraUtils.emptyIssue(cfg.type));
          renderIssues(false);
          syncFooter();
        });

        editorEl.addEventListener("input", syncFooter);
      }
    }

    function renderLeftPane() {
      return ''
        + '<div class="export-modal__section" id="' + cfg.prefix + '-token-section">'
        + '<h4>Connection</h4>'
        + '<div id="' + cfg.prefix + '-token-status">Checking connection...</div>'
        + '</div>'

        + '<div class="export-modal__section" id="' + cfg.prefix + '-destination-section" hidden>'
        + '<h4>Destination ' + window.JiraUtils.esc(cfg.destinationLabel) + '</h4>'
        + '<select id="' + cfg.prefix + '-project-select" class="input input--sm">'
        + '<option value="">- Select ' + window.JiraUtils.esc(cfg.destinationLabel) + ' -</option>'
        + '</select>'
        + '</div>'

        + '<div class="export-modal__section">'
        + '<div class="export-modal__section-head">'
        + '<h4>' + window.JiraUtils.esc(cfg.itemLabelPlural) + ' <span class="export-modal__count-badge" id="' + cfg.prefix + '-issue-count">0</span></h4>'
        + '<button type="button" class="btn btn--sm btn--primary" id="' + cfg.prefix + '-add-issue-btn">Add ' + window.JiraUtils.esc(cfg.itemLabel) + '</button>'
        + '</div>'
        + '<div id="' + cfg.prefix + '-editor-issues" class="jira-editor__issues"></div>'
        + '</div>';
    }

    return {
      label: cfg.label,

      referenceUrl: function (ctx) {
        if (!ctx.sessionId || !ctx.discussionId) return null;
        return "/jira/" + ctx.sessionId + "/reference/" + encodeURIComponent(ctx.discussionId) + "/";
      },

      renderLeftPane: function () {
        return renderLeftPane();
      },

      onOpen: function (ctx, baseAPI) {
        state = {
          issues: [],
          exported: false,
          defaults: {},
          baseAPI: baseAPI,
        };
        bindLeftPaneEvents();
        checkStatus(ctx);
      },

      onExtract: function (ctx) {
        onExtract(ctx);
      },

      onSave: function (ctx) {
        onSave(ctx);
      },

      onPush: function (ctx) {
        onPush(ctx);
      },

      syncFooter: function (ctx) {
        var hasIssues = !!(state.issues && state.issues.length);
        var projectKey = (document.getElementById(cfg.prefix + "-project-select") || {}).value || "";
        return {
          extractHidden: !ctx.discussionId,
          extractDisabled: !ctx.discussionId,
          saveDisabled: !hasIssues,
          pushHidden: !hasIssues,
          pushDisabled: !hasIssues || !projectKey,
        };
      },
    };
  }

  window.JiraAdapterFactory = {
    create: create,
  };
})();
