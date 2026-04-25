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
      existingIssuesCatalog: [],
      globalSelections: { sprint: "" },
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
      if (sprintSel) sprintSel.value = state.globalSelections.sprint || "";
    }

    function renderGlobalDropdownOptions() {
      if (!isSoftware()) return;

      var sprintSel = document.getElementById(cfg.prefix + "-default-sprint-select");

      if (sprintSel) {
        var sprintHtml = "";
        (state.fieldOptions.sprint || [{ value: "", label: "Backlog" }]).forEach(function (opt) {
          var value = window.JiraUtils.esc(String((opt && opt.value) || ""));
          var label = window.JiraUtils.esc(String((opt && opt.label) || value || "Backlog"));
          sprintHtml += '<option value="' + value + '">' + label + '</option>';
        });
        sprintSel.innerHTML = sprintHtml;
      }

      setGlobalDropdowns();
    }

    function applyGlobalSelectionsToIssues() {
      if (!isSoftware() || state.exported) return;
      state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);
      state.issues = (state.issues || []).map(function (issue) {
        var next = Object.assign({}, issue);
        next.sprint = state.globalSelections.sprint || "";
        return next;
      });
      if (!state.issues.length) {
        var empty = window.JiraUtils.emptyIssue(cfg.type);
        empty.sprint = state.globalSelections.sprint || "";
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
      fallback.existing_issue_key = [{ value: "", label: "New" }];
      return fallback;
    }

    function _normText(v) {
      return String(v || "")
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    }

    function _tokens(v) {
      return _normText(v)
        .split(" ")
        .filter(function (t) { return t.length >= 3; });
    }

    function _fuzzySummaryScore(a, b) {
      var na = _normText(a);
      var nb = _normText(b);
      if (!na || !nb) return 0;
      if (na === nb) return 1;
      if (na.indexOf(nb) !== -1 || nb.indexOf(na) !== -1) return 0.8;

      var ta = _tokens(na);
      var tb = _tokens(nb);
      if (!ta.length || !tb.length) return 0;

      var setA = {};
      var setB = {};
      ta.forEach(function (t) { setA[t] = true; });
      tb.forEach(function (t) { setB[t] = true; });

      var intersection = 0;
      var union = 0;
      var seen = {};

      Object.keys(setA).forEach(function (k) {
        seen[k] = true;
        union += 1;
        if (setB[k]) intersection += 1;
      });
      Object.keys(setB).forEach(function (k) {
        if (!seen[k]) union += 1;
      });

      return union ? (intersection / union) : 0;
    }

    function _normalizedSummary(v) {
      return _normText(v || "");
    }

    function _issueMapByTempId() {
      var out = {};
      (state.issues || []).forEach(function (it) {
        var tid = String((it && it.temp_id) || "").trim();
        if (tid) out[tid] = it;
      });
      return out;
    }

    function _candidateExistingRows(issue, opts) {
      opts = opts || {};
      var selectedType = String((issue && issue.issue_type) || "").trim().toLowerCase();
      var selectedSummary = String((issue && issue.summary) || "").trim();
      var catalog = state.existingIssuesCatalog || [];

      var parentKey = "";
      var parentTempId = String((issue && issue.parent_temp_id) || "").trim();
      if (parentTempId) {
        var byTempId = _issueMapByTempId();
        var parentIssue = byTempId[parentTempId] || null;
        parentKey = String((parentIssue && parentIssue.existing_issue_key) || "").trim();
      }

      var sameType = (catalog || []).filter(function (row) {
        var rowType = String((row && row.issue_type) || "").trim().toLowerCase();
        return !!row && !!String(row.key || "").trim() && (!selectedType || rowType === selectedType);
      });

      if (parentKey) {
        sameType = sameType.filter(function (row) {
          var rowParent = String((row && row.parent_key) || "").trim();
          return !!rowParent && rowParent === parentKey;
        });
      }

      var scored = sameType
        .map(function (row) {
          var score = _fuzzySummaryScore(selectedSummary, row.summary || "");
          var exact = !!selectedSummary && _normalizedSummary(selectedSummary) === _normalizedSummary(row.summary || "");
          return {
            key: String(row.key || "").trim(),
            summary: String(row.summary || "").trim(),
            score: score,
            exact: exact,
          };
        })
        .sort(function (a, b) {
          if ((b.exact ? 1 : 0) !== (a.exact ? 1 : 0)) return (b.exact ? 1 : 0) - (a.exact ? 1 : 0);
          if (b.score !== a.score) return b.score - a.score;
          return a.key.localeCompare(b.key);
        });

      var threshold = (typeof opts.threshold === "number") ? opts.threshold : 0.35;
      var matched = scored.filter(function (row) {
        return !selectedSummary || row.exact || row.score >= threshold;
      });

      if (!matched.length && sameType.length) {
        matched = sameType
          .map(function (row) {
            return {
              key: String(row.key || "").trim(),
              summary: String(row.summary || "").trim(),
              score: 0,
              exact: false,
            };
          })
          .sort(function (a, b) {
            return a.key.localeCompare(b.key);
          });
      }

      return matched;
    }

    function _autoSelectExistingBySummary(cascadeOnly) {
      if (!isSoftware() || state.exported) return;

      var changed = false;
      var byTempId = _issueMapByTempId();
      var childrenByParent = {};
      (state.issues || []).forEach(function (it) {
        var pid = String((it && it.parent_temp_id) || "").trim();
        if (!pid) return;
        (childrenByParent[pid] = childrenByParent[pid] || []).push(it);
      });

      var queue = [];
      (state.issues || []).forEach(function (it) {
        var pid = String((it && it.parent_temp_id) || "").trim();
        if (!pid || !byTempId[pid]) queue.push(it);
      });

      while (queue.length) {
        var issue = queue.shift();
        var current = String((issue && issue.existing_issue_key) || "").trim();
        var candidates = _candidateExistingRows(issue, { threshold: 0.35 });
        var allowedKeys = {};
        candidates.forEach(function (row) { allowedKeys[row.key] = true; });

        if (current && !allowedKeys[current]) {
          issue.existing_issue_key = "";
          current = "";
          changed = true;
        }

        if (!cascadeOnly && !current) {
          var exact = candidates.filter(function (row) { return row.exact; });
          if (exact.length === 1) {
            issue.existing_issue_key = exact[0].key;
            changed = true;
          }
        }

        var tid = String((issue && issue.temp_id) || "").trim();
        (childrenByParent[tid] || []).forEach(function (child) { queue.push(child); });
      }

      if (changed) {
        renderIssues(false);
      }
    }

    function _existingIssueOptionsFor(issue) {
      var currentKey = String((issue && issue.existing_issue_key) || "").trim();
      var matched = _candidateExistingRows(issue, { threshold: 0.35 });

      var options = [{ value: "", label: "New" }];
      matched.forEach(function (row) {
        var label = row.key + (row.summary ? " - " + row.summary : "");
        options.push({ value: row.key, label: label });
      });

      // Preserve currently selected key even if it no longer matches the active filter.
      if (currentKey && !options.some(function (opt) { return String(opt.value || "") === currentKey; })) {
        options.push({ value: currentKey, label: currentKey + " - Selected" });
      }

      return options;
    }

    function applyMetadataOptions(meta, useFallback) {
      if (!isSoftware()) return;

      if (!meta || useFallback) {
        state.existingIssuesCatalog = [];
        state.fieldOptions = fallbackSoftwareOptions();
        state.fieldOptions.existing_issue_key = _existingIssueOptionsFor;
        state.globalSelections.sprint = "";
        renderGlobalDropdownOptions();
        setGlobalDropdowns();
        applyGlobalSelectionsToIssues();
        _autoSelectExistingBySummary(false);
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
      state.existingIssuesCatalog = Array.isArray(meta.existing_issues) ? meta.existing_issues : [];

      var defaultOptions = window.JiraUtils.getDefaultFieldOptions("software");
      state.fieldOptions = {
        issue_type: issueTypes.length ? issueTypes : defaultOptions.issue_type,
        priority: priorities.length ? priorities : defaultOptions.priority,
        sprint: sprints,
        existing_issue_key: _existingIssueOptionsFor,
      };

      state.globalSelections.sprint = "";
      renderGlobalDropdownOptions();
      setGlobalDropdowns();
      applyGlobalSelectionsToIssues();
      _autoSelectExistingBySummary(false);
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
              if (!next.temp_id) next.temp_id = window.JiraUtils.genTempId();
              if (next.parent_temp_id === undefined) next.parent_temp_id = null;
              if (next.existing_issue_key === undefined || next.existing_issue_key === null) {
                next.existing_issue_key = "";
              }
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
              if (!next.temp_id) next.temp_id = window.JiraUtils.genTempId();
              if (next.parent_temp_id === undefined) next.parent_temp_id = null;
              if (next.existing_issue_key === undefined || next.existing_issue_key === null) {
                next.existing_issue_key = "";
              }
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

      setStatus("Saving draft and pushing to " + cfg.label + "...");
      var btn = document.getElementById("export-modal-push-btn");
      if (btn) btn.disabled = true;

      // Always persist the latest UI state before push so DB and exported
      // payload stay in sync even when Save is not clicked explicitly.
      api(
        ctx,
        "POST",
        "/jira/" + ctx.sessionId + "/export/" + encodeURIComponent(ctx.discussionId) + "/" + cfg.type + "/",
        {
          items: state.issues,
          source: "manual",
        }
      )
        .then(function (saved) {
          state.issues = (saved && saved.export && saved.export.issues) || state.issues;
          return api(ctx, "POST", "/jira/" + ctx.sessionId + "/push/" + cfg.type + "/", {
            project_key: projectKey,
            discussion_id: ctx.discussionId,
            items: state.issues,
          });
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

    // Default child issue type by parent issue type. Falls back to "Task".
    var CHILD_TYPE_DEFAULTS = {
      "epic":     "Story",
      "feature":  "Story",
      "story":    "Task",
      "task":     "Sub-task",
      "sub-task": "Sub-task",
      "subtask":  "Sub-task",
      "bug":      "Sub-task",
    };

    function _defaultChildType(parentType) {
      var key = String(parentType || "").trim().toLowerCase();
      return CHILD_TYPE_DEFAULTS[key] || "Task";
    }

    // Recursively collect a temp_id and all of its descendant temp_ids
    // from the current state.issues list.
    function _descendantIds(rootTempId) {
      var byParent = {};
      (state.issues || []).forEach(function (it) {
        var pid = it && it.parent_temp_id;
        if (!pid) return;
        (byParent[pid] = byParent[pid] || []).push(it.temp_id);
      });
      var out = [];
      var stack = [rootTempId];
      while (stack.length) {
        var id = stack.pop();
        out.push(id);
        (byParent[id] || []).forEach(function (cid) { stack.push(cid); });
      }
      return out;
    }

    function bindLeftPaneEvents(ctx) {
      var sel = document.getElementById(cfg.prefix + "-project-select");
      if (sel) {
        sel.addEventListener("change", function () {
          syncFooter();
          loadProjectMetadata(ctx, sel.value || "");
        });
      }

      if (isSoftware()) {
        var sprintSel = document.getElementById(cfg.prefix + "-default-sprint-select");
        if (sprintSel) {
          sprintSel.addEventListener("change", function () {
            state.globalSelections.sprint = sprintSel.value || "";
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
          // Persist any in-flight edits before re-rendering.
          state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);
          var empty = window.JiraUtils.emptyIssue(cfg.type);
          if (isSoftware()) {
            empty.sprint = state.globalSelections.sprint || "";
            empty.parent_temp_id = null;
            empty.issue_type = "Epic";
            empty.existing_issue_key = "";
          }
          state.issues.push(empty);
          renderIssues(false);
          syncFooter();
        });
      }

      var editorEl = document.getElementById(cfg.prefix + "-editor-issues");
      if (editorEl) {
        editorEl.addEventListener("click", function (e) {
          // Caret toggle is allowed even when locked (read-only browsing).
          var caretBtn = e.target.closest(".js-toggle-issue");
          if (caretBtn) {
            var card = caretBtn.closest(".jira-issue-card");
            if (!card) return;
            var collapsed = card.classList.toggle("jira-issue-card--collapsed");
            caretBtn.textContent = collapsed ? "\u25B8" : "\u25BE";
            window.JiraUtils.setCardCollapsed(
              card.getAttribute("data-temp-id") || "",
              collapsed
            );
            return;
          }

          if (state.exported) {
            setStatus("Export is locked. Click Extract Items to unlock.");
            return;
          }

          var addChildBtn = e.target.closest(".js-add-child");
          if (addChildBtn && isSoftware()) {
            var parentTempId = addChildBtn.getAttribute("data-temp-id") || "";
            if (!parentTempId) return;
            state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);
            var parent = (state.issues || []).filter(function (it) { return it.temp_id === parentTempId; })[0];
            var child = window.JiraUtils.emptyIssue(cfg.type);
            child.parent_temp_id = parentTempId;
            child.sprint = state.globalSelections.sprint || "";
            child.issue_type = _defaultChildType(parent && parent.issue_type);
            child.existing_issue_key = "";
            // Ensure parent stays expanded so the new child is visible.
            window.JiraUtils.setCardCollapsed(parentTempId, false);
            state.issues.push(child);
            renderIssues(false);
            syncFooter();
            return;
          }

          var delBtn = e.target.closest(".js-delete-issue");
          if (!delBtn) return;

          state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);

          if (isSoftware()) {
            var tempId = delBtn.getAttribute("data-temp-id") || "";
            if (!tempId) return;
            var toRemove = _descendantIds(tempId);
            if (toRemove.length > 1) {
              var ok = window.confirm("Delete this issue and all " + (toRemove.length - 1) + " nested child issue(s)?");
              if (!ok) return;
            }
            var removeSet = {};
            toRemove.forEach(function (id) { removeSet[id] = true; });
            state.issues = (state.issues || []).filter(function (it) { return !removeSet[it.temp_id]; });
            if (!state.issues.length) {
              var seed = window.JiraUtils.emptyIssue(cfg.type);
              seed.parent_temp_id = null;
              seed.issue_type = "Epic";
              seed.sprint = state.globalSelections.sprint || "";
              seed.existing_issue_key = "";
              state.issues.push(seed);
            }
          } else {
            var idx = parseInt(delBtn.getAttribute("data-issue-index") || "-1", 10);
            if (idx < 0) return;
            state.issues.splice(idx, 1);
            if (!state.issues.length) state.issues.push(window.JiraUtils.emptyIssue(cfg.type));
          }
          renderIssues(false);
          syncFooter();
        });

        editorEl.addEventListener("input", syncFooter);
        editorEl.addEventListener("change", function (e) {
          syncFooter();

          if (!isSoftware() || state.exported) return;

          var target = e && e.target;
          if (!target || !target.getAttribute) return;

          var field = target.getAttribute("data-field") || "";
          if (field !== "issue_type" && field !== "summary" && field !== "existing_issue_key") return;

          // Re-render so Existing Issue options recompute from current
          // summary + issue type, and read-only state updates immediately
          // after selecting an existing Jira key.
          state.issues = window.JiraUtils.collectIssuesFromEditor(cfg.prefix + "-editor-issues", cfg.type);
          _autoSelectExistingBySummary(field === "existing_issue_key");
          renderIssues(false);
          syncFooter();
        });
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
          existingIssuesCatalog: [],
          globalSelections: { sprint: "" },
          baseAPI: baseAPI,
        };
        bindLeftPaneEvents(ctx);
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
