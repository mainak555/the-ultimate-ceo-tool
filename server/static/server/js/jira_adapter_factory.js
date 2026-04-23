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
      pushLabel: config.pushLabel || config.label,
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
      fieldOptions: (window.JiraUtils && window.JiraUtils.getDefaultFieldOptions)
        ? window.JiraUtils.getDefaultFieldOptions(cfg.type)
        : {},
      metadataByProject: {},
      globalSelections: { sprint: "", epic: "" },
      baseAPI: null,
    };

    function isSoftware() {
      return cfg.type === "software";
    }

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
        isExported || false,
        state.fieldOptions || {}
      );
    }

    function setGlobalDropdowns() {
      if (!isSoftware()) return;
      var sprintSel = document.getElementById(cfg.prefix + "-default-sprint-select");
      var epicSel = document.getElementById(cfg.prefix + "-default-epic-select");
      if (sprintSel) sprintSel.value = state.globalSelections.sprint || "";
      if (epicSel) epicSel.value = state.globalSelections.epic || "";
    }

    function renderGlobalDropdownOptions() {
      if (!isSoftware()) return;

      var sprintSel = document.getElementById(cfg.prefix + "-default-sprint-select");
      var epicSel = document.getElementById(cfg.prefix + "-default-epic-select");

      if (sprintSel) {
        var sprintHtml = "";
        (state.fieldOptions.sprint || [{ value: "", label: "Backlog" }]).forEach(function (opt) {
          var value = window.JiraUtils.esc(String((opt && opt.value) || ""));
          var label = window.JiraUtils.esc(String((opt && opt.label) || value || "Backlog"));
          sprintHtml += '<option value="' + value + '">' + label + '</option>';
        });
        sprintSel.innerHTML = sprintHtml;
      }

      if (epicSel) {
        var epicHtml = "";
        (state.fieldOptions.epic || [{ value: "", label: "None" }]).forEach(function (opt) {
          var value = window.JiraUtils.esc(String((opt && opt.value) || ""));
          var label = window.JiraUtils.esc(String((opt && opt.label) || value || "None"));
          epicHtml += '<option value="' + value + '">' + label + '</option>';
        });
        epicSel.innerHTML = epicHtml;
      }

      setGlobalDropdowns();
    }

    function applyGlobalSelectionsToIssues() {
      if (!isSoftware() || state.exported) return;
      state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);
      state.issues = (state.issues || []).map(function (issue) {
        var next = Object.assign({}, issue);
        next.sprint = state.globalSelections.sprint || "";
        next.epic = state.globalSelections.epic || "";
        return next;
      });
      if (!state.issues.length) {
        var empty = window.JiraUtils.emptyIssue(cfg.type);
        empty.sprint = state.globalSelections.sprint || "";
        empty.epic = state.globalSelections.epic || "";
        state.issues.push(empty);
      }
      renderIssues(false);
    }

    function toOptionRows(list, fallbackLabel) {
      var rows = [];
      var seenLabels = {};
      (list || []).forEach(function (row) {
        if (!row) return;
        var value = String(row.id || row.value || row.key || row.name || "").trim();
        var label = String(row.name || row.label || row.key || value || "").trim();
        if (!value && !label) return;
        var finalLabel = label || value || fallbackLabel || "Option";
        var dedupeKey = finalLabel.toLowerCase();
        if (seenLabels[dedupeKey]) return;
        seenLabels[dedupeKey] = true;
        rows.push({ value: value || finalLabel, label: finalLabel });
      });
      return rows;
    }

    function fallbackSoftwareOptions() {
      var fallback = window.JiraUtils.getDefaultFieldOptions("software");
      fallback.sprint = [{ value: "", label: "Backlog" }];
      fallback.epic = [{ value: "", label: "None" }];
      return fallback;
    }

    function applyMetadataOptions(meta, useFallback) {
      if (!isSoftware()) return;

      if (!meta || useFallback) {
        state.fieldOptions = fallbackSoftwareOptions();
        state.globalSelections.sprint = "";
        state.globalSelections.epic = "";
        renderGlobalDropdownOptions();
        setGlobalDropdowns();
        applyGlobalSelectionsToIssues();
        syncFooter();
        return;
      }

      var issueTypes = toOptionRows(meta.issue_types, "Issue Type").map(function (row) {
        return { value: row.label, label: row.label };
      });
      var priorities = toOptionRows(meta.priorities, "Priority").map(function (row) {
        return { value: row.label, label: row.label };
      });
      var sprints = [{ value: "", label: "Backlog" }].concat(toOptionRows(meta.sprints, "Sprint"));
      var epics = [{ value: "", label: "None" }].concat(
        (meta.epics || []).map(function (epic) {
          var value = String(epic.id || epic.key || epic.name || "").trim();
          var name = String(epic.name || epic.key || value).trim();
          var key = String(epic.key || "").trim();
          return { value: value || key || name, label: key ? (key + " - " + name) : name };
        }).filter(function (row) { return !!(row.value || row.label); })
      );

      var defaultOptions = window.JiraUtils.getDefaultFieldOptions("software");
      state.fieldOptions = {
        issue_type: issueTypes.length ? issueTypes : defaultOptions.issue_type,
        priority: priorities.length ? priorities : defaultOptions.priority,
        sprint: sprints,
        epic: epics,
      };

      state.globalSelections.sprint = "";
      state.globalSelections.epic = "";
      renderGlobalDropdownOptions();
      setGlobalDropdowns();
      applyGlobalSelectionsToIssues();
      syncFooter();
    }

    function loadProjectMetadata(ctx, projectKey) {
      if (!isSoftware()) return Promise.resolve();

      projectKey = (projectKey || "").trim();
      if (!projectKey) {
        applyMetadataOptions(null, true);
        return Promise.resolve();
      }

      var cached = state.metadataByProject[projectKey];
      if (cached) {
        applyMetadataOptions(cached, false);
        return Promise.resolve();
      }

      setStatus("Loading project metadata...");
      return api(
        ctx,
        "GET",
        "/jira/" + ctx.sessionId + "/metadata/" + cfg.type + "/?project_key=" + encodeURIComponent(projectKey)
      )
        .then(function (data) {
          state.metadataByProject[projectKey] = data || {};
          applyMetadataOptions(data, false);
          setStatus("");
        })
        .catch(function (err) {
          applyMetadataOptions(null, true);
          setStatus("Using fallback options: " + err.message);
        });
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

          var connectionLabel = (cfg.pushLabel || cfg.label || "Jira").trim();

          if (d.configured) {
            el.innerHTML =
              '<span class="export-modal__token-status export-modal__token-status--valid">'
              + window.JiraUtils.esc(connectionLabel) + ' Connected</span>';
            state.defaults = {
              default_project_key: (d.default_project_key || "").trim(),
              default_project_name: (d.default_project_name || "").trim(),
            };
            showDestination(ctx);
          } else {
            var err = connectionLabel + " is not configured in Project Settings.";
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
          loadProjectMetadata(ctx, sel.value || "");
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
          if (isSoftware() && !state.exported) {
            state.issues = state.issues.map(function (issue) {
              var next = Object.assign({}, issue);
              if (next.sprint === undefined || next.sprint === null) next.sprint = "";
              if (next.epic === undefined || next.epic === null) next.epic = "";
              return next;
            });
          }
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
          if (isSoftware()) {
            state.issues = state.issues.map(function (issue) {
              var next = Object.assign({}, issue);
              next.sprint = state.globalSelections.sprint || "";
              next.epic = state.globalSelections.epic || "";
              return next;
            });
          }
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
      if (sel) {
        sel.addEventListener("change", function () {
          syncFooter();
          loadProjectMetadata(_ctx, sel.value || "");
        });
      }

      if (isSoftware()) {
        var sprintSel = document.getElementById(cfg.prefix + "-default-sprint-select");
        var epicSel = document.getElementById(cfg.prefix + "-default-epic-select");
        if (sprintSel) {
          sprintSel.addEventListener("change", function () {
            state.globalSelections.sprint = sprintSel.value || "";
            applyGlobalSelectionsToIssues();
            syncFooter();
          });
        }
        if (epicSel) {
          epicSel.addEventListener("change", function () {
            state.globalSelections.epic = epicSel.value || "";
            applyGlobalSelectionsToIssues();
            syncFooter();
          });
        }
      }

      var addBtn = document.getElementById(cfg.prefix + "-add-issue-btn");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          if (state.exported) {
            setStatus("Export is locked. Click Extract Items to unlock.");
            return;
          }
          var empty = window.JiraUtils.emptyIssue(cfg.type);
          if (isSoftware()) {
            empty.sprint = state.globalSelections.sprint || "";
            empty.epic = state.globalSelections.epic || "";
          }
          state.issues.push(empty);
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
        editorEl.addEventListener("change", syncFooter);
      }
    }

    function renderLeftPane() {
      var destinationHtml = ''
        + '<div class="export-modal__section" id="' + cfg.prefix + '-destination-section" hidden>'
        + '<h4>Destination ' + window.JiraUtils.esc(cfg.destinationLabel) + '</h4>';

      if (isSoftware()) {
        destinationHtml += ''
          + '<div class="cascade-select">'
          + '<div class="cascade-select__group">'
          + '<label for="' + cfg.prefix + '-project-select">Project</label>'
          + '<select id="' + cfg.prefix + '-project-select" class="input input--sm">'
          + '<option value="">- Select ' + window.JiraUtils.esc(cfg.destinationLabel) + ' -</option>'
          + '</select>'
          + '</div>'
          + '<div class="cascade-select__group">'
          + '<label for="' + cfg.prefix + '-default-sprint-select">Sprint</label>'
          + '<select id="' + cfg.prefix + '-default-sprint-select" class="input input--sm">'
          + '<option value="" selected>Backlog</option>'
          + '</select>'
          + '</div>'
          + '<div class="cascade-select__group">'
          + '<label for="' + cfg.prefix + '-default-epic-select">Epic</label>'
          + '<select id="' + cfg.prefix + '-default-epic-select" class="input input--sm">'
          + '<option value="" selected>None</option>'
          + '</select>'
          + '</div>'
          + '</div>';
      } else {
        destinationHtml += ''
          + '<select id="' + cfg.prefix + '-project-select" class="input input--sm">'
          + '<option value="">- Select ' + window.JiraUtils.esc(cfg.destinationLabel) + ' -</option>'
          + '</select>';
      }
      destinationHtml += '</div>';

      return ''
        + '<div class="export-modal__section" id="' + cfg.prefix + '-token-section">'
        + '<h4>Connection</h4>'
        + '<div id="' + cfg.prefix + '-token-status">Checking connection...</div>'
        + '</div>'

        + destinationHtml

        + '<div class="export-modal__section jira-workspace-section">'
        + '<div class="export-modal__section-head">'
        + '<h4>' + window.JiraUtils.esc(cfg.itemLabelPlural) + ' <span class="export-modal__count-badge" id="' + cfg.prefix + '-issue-count">0</span></h4>'
        + '<button type="button" class="btn btn--sm btn--primary export-modal__context-add-btn" id="' + cfg.prefix + '-add-issue-btn">Add ' + window.JiraUtils.esc(cfg.itemLabel) + '</button>'
        + '</div>'
        + '<div class="export-modal__section-divider"></div>'
        + '<div id="' + cfg.prefix + '-editor-issues" class="jira-editor__issues"></div>'
        + '</div>';
    }

    return {
      label: cfg.label,
      pushLabel: cfg.pushLabel,

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
          fieldOptions: (window.JiraUtils && window.JiraUtils.getDefaultFieldOptions)
            ? window.JiraUtils.getDefaultFieldOptions(cfg.type)
            : {},
          metadataByProject: {},
          globalSelections: { sprint: "", epic: "" },
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
