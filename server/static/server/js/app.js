/**
 * app.js — Minimal client-side helpers for the agent configuration SPA.
 *
 * Handles:
 *   - Adding / removing agent cards dynamically
 *   - Updating the form action URL when creating a new project
 *   - Injecting the Secret Key into HTMX requests
 *   - Toggling human gate controls and iteration limits
 *   - Auto-dismissing toast alerts
 */

document.addEventListener("DOMContentLoaded", function () {
  function getSecretKeyInput() {
    return document.getElementById("global-secret-key");
  }

  function updateSubmitState() {
    var keyInput = getSecretKeyInput();
    var hasSecret = !!(keyInput && keyInput.value.trim());

    document.querySelectorAll(".config-form button[type='submit'], .config-form .js-requires-secret").forEach(function (button) {
      if (button.id === "trello-generate-token-btn") {
        return;
      }
      button.disabled = !hasSecret;
      button.title = hasSecret ? "" : "Enter the Secret Key in the header before saving.";
    });

    syncTrelloGenerateTokenState();

    // Show/hide delete buttons in the sidebar
    document.querySelectorAll(".sidebar__delete").forEach(function (btn) {
      btn.hidden = !hasSecret;
    });

    // Auto-load Trello defaults when secret key becomes available.
    if (hasSecret) {
      maybeLoadTrelloCascadeForCurrentProject();
    }
  }

  function syncHumanGateFields() {
    var enabledInput = document.getElementById("human-gate-enabled");
    var fields = document.getElementById("human-gate-fields");
    if (!fields) return;

    var enabled = !!(enabledInput && enabledInput.checked);
    fields.hidden = !enabled;

    fields.querySelectorAll("input, select, textarea").forEach(function (field) {
      field.disabled = !enabled;
    });
  }

  function syncMaxIterationsLimit() {
    var enabledInput = document.getElementById("human-gate-enabled");
    var maxIterationsInput = document.getElementById("max_iterations");
    if (!maxIterationsInput) return;

    var limit = enabledInput && enabledInput.checked ? 100 : 10;
    maxIterationsInput.max = String(limit);

    var currentValue = parseInt(maxIterationsInput.value || "0", 10);
    if (currentValue > limit) {
      maxIterationsInput.value = String(limit);
    }
  }

  function syncTeamTypeFields() {
    var teamTypeSelect = document.getElementById("team_type");
    var selectorFields = document.getElementById("selector-fields");
    if (!teamTypeSelect || !selectorFields) return;

    var isSelector = teamTypeSelect.value === "selector";
    selectorFields.hidden = !isSelector;

    selectorFields.querySelectorAll("input, select, textarea").forEach(function (field) {
      field.disabled = !isSelector;
    });
  }

  function syncIntegrationsFields() {
    var enabledInput = document.getElementById("integrations-enabled");
    var fields = document.getElementById("integrations-fields");
    if (!fields) return;

    var enabled = !!(enabledInput && enabledInput.checked);
    fields.hidden = !enabled;

    // Trello sub-toggle
    var trelloEnabled = document.getElementById("integrations-trello-enabled");
    var trelloFields = document.getElementById("integrations-trello-fields");
    if (trelloFields) {
      var trelloOn = enabled && !!(trelloEnabled && trelloEnabled.checked);
      trelloFields.hidden = !trelloOn;
      trelloFields.querySelectorAll("input, select, textarea").forEach(function (f) {
        // Keep the token display always disabled (it's a read-only indicator)
        if (f.id === "trello-token-display") return;
        f.disabled = !trelloOn;
      });
    }

    // Sync export agent dropdown options from current agent names
    syncExportAgentDropdown();
  }

  function syncExportAgentDropdown() {
    var dropdown = document.getElementById("integrations-export-agent");
    if (!dropdown) return;

    var currentValue = dropdown.value;
    var container = document.getElementById("agents-container");
    if (!container) return;

    // Collect current agent names
    var agentNames = [];
    container.querySelectorAll(".agent-card").forEach(function (card) {
      var nameInput = card.querySelector("[name$='[name]']");
      if (nameInput && nameInput.value.trim()) {
        agentNames.push(nameInput.value.trim());
      }
    });

    // Preserve the "all" option and rebuild
    var html = '<option value="">— All agents (show export on every message) —</option>';
    agentNames.forEach(function (name) {
      var selected = name === currentValue ? " selected" : "";
      html += '<option value="' + name + '"' + selected + '>' + name + '</option>';
    });
    dropdown.innerHTML = html;
  }

  function syncTrelloGenerateTokenState() {
    var button = document.getElementById("trello-generate-token-btn");
    if (!button) return;

    // Keep button disabled while auth flow is in progress.
    if (button.dataset.loading === "true") return;

    var createForm = document.getElementById("config-form-create");
    var isCreateMode = !!createForm;
    var projectIdEl = document.getElementById("config-project-id");
    var projectId = projectIdEl ? projectIdEl.value.trim() : "";
    var appNameEl = document.getElementById("trello-app-name");
    var appName = appNameEl ? appNameEl.value.trim() : "";
    var keyInput = getSecretKeyInput();
    var hasSecret = !!(keyInput && keyInput.value.trim());

    var integrationsEnabled = document.getElementById("integrations-enabled");
    var trelloEnabled = document.getElementById("integrations-trello-enabled");
    var trelloIsOn = !!(
      (!integrationsEnabled || integrationsEnabled.checked) &&
      (!trelloEnabled || trelloEnabled.checked)
    );

    var canGenerate = !isCreateMode && !!projectId && !!appName && hasSecret && trelloIsOn;
    button.disabled = !canGenerate;

    if (isCreateMode) {
      button.title = "Save the configuration first to generate a token.";
      return;
    }
    if (!projectId) {
      button.title = "Save the configuration first to generate a token.";
      return;
    }
    if (!appName) {
      button.title = "Enter Trello App Name before generating a token.";
      return;
    }
    if (!hasSecret) {
      button.title = "Enter the Secret Key in the header before generating a token.";
      return;
    }
    if (!trelloIsOn) {
      button.title = "Enable Trello integration to generate a token.";
      return;
    }
    button.title = "";
  }

  function syncFormState() {
    syncHumanGateFields();
    syncMaxIterationsLimit();
    syncTeamTypeFields();
    syncIntegrationsFields();
    updateSubmitState();
  }

  document.body.addEventListener("htmx:configRequest", function (e) {
    var keyInput = getSecretKeyInput();
    var secretKey = keyInput ? keyInput.value.trim() : "";
    if (secretKey) {
      e.detail.headers["X-App-Secret-Key"] = secretKey;
    }
  });

  // -----------------------------------------------------------------------
  // Agent card: Add
  // -----------------------------------------------------------------------
  document.body.addEventListener("click", function (e) {
    if (!e.target.matches("#add-agent-btn")) return;

    var container = document.getElementById("agents-container");
    if (!container) return;

    var template = document.getElementById("agent-card-template");
    if (!template) return;

    // Determine next index
    var cards = container.querySelectorAll(".agent-card");
    var nextIdx = cards.length;

    // Clone template content and replace __IDX__ placeholders
    var clone = template.content.cloneNode(true);
    var html = clone.firstElementChild.outerHTML.replace(/__IDX__/g, nextIdx);

    container.insertAdjacentHTML("beforeend", html);
    syncFormState();
  });

  // -----------------------------------------------------------------------
  // Agent card: Remove
  // -----------------------------------------------------------------------
  document.body.addEventListener("click", function (e) {
    if (!e.target.matches(".remove-agent-btn")) return;

    var card = e.target.closest(".agent-card");
    if (!card) return;

    // Don't remove the last agent
    var container = document.getElementById("agents-container");
    if (container && container.querySelectorAll(".agent-card").length <= 1) {
      alert("At least one agent is required.");
      return;
    }

    card.remove();
    reindexAgents();
  });

  // -----------------------------------------------------------------------
  // Re-index agent card field names after removal
  // -----------------------------------------------------------------------
  function reindexAgents() {
    var container = document.getElementById("agents-container");
    if (!container) return;

    var cards = container.querySelectorAll(".agent-card");
    cards.forEach(function (card, idx) {
      card.setAttribute("data-agent-index", idx);

      // Update the agent number label
      var numEl = card.querySelector(".agent-card__number");
      if (numEl) numEl.textContent = "Agent #" + (idx + 1);

      // Update all input/select/textarea name attributes
      card.querySelectorAll("[name]").forEach(function (el) {
        el.name = el.name.replace(/agents\[\d+\]/, "agents[" + idx + "]");
      });
    });
  }

  document.body.addEventListener("input", function (e) {
    if (e.target.id === "global-secret-key") {
      updateSubmitState();
    }
    if (e.target.id === "trello-app-name") {
      syncTrelloGenerateTokenState();
    }
  });

  document.body.addEventListener("change", function (e) {
    if (e.target.id === "human-gate-enabled") {
      syncHumanGateFields();
      syncMaxIterationsLimit();
    }
    if (e.target.id === "team_type") {
      syncTeamTypeFields();
    }
    if (e.target.id === "integrations-enabled" ||
        e.target.id === "integrations-trello-enabled") {
      syncIntegrationsFields();
      syncTrelloGenerateTokenState();
    }
  });

  // -----------------------------------------------------------------------
  // Auto-dismiss toast alerts after 4 seconds
  // -----------------------------------------------------------------------
  document.body.addEventListener("htmx:afterSwap", function () {
    syncFormState();
    maybeLoadTrelloCascadeForCurrentProject();

    var toast = document.getElementById("toast");
    if (toast) {
      setTimeout(function () {
        toast.style.transition = "opacity 0.3s";
        toast.style.opacity = "0";
        setTimeout(function () { toast.remove(); }, 300);
      }, 4000);
    }
  });

  syncFormState();

  // =========================================================================
  // Trello Config — token generation & cascade dropdowns (config page)
  // =========================================================================

  function getTrelloProjectId() {
    var el = document.getElementById("config-project-id");
    return el ? el.value.trim() : "";
  }

  function getTrelloSecretKey() {
    var ki = getSecretKeyInput();
    return ki ? ki.value.trim() : "";
  }

  function trelloHeaders() {
    var csrfInput = document.querySelector("[name=csrfmiddlewaretoken]");
    return {
      "Content-Type": "application/json",
      "X-App-Secret-Key": getTrelloSecretKey(),
      "X-CSRFToken": csrfInput ? csrfInput.value : ""
    };
  }

  // --- Token generation via popup ---

  function _setTokenBtnLoading(loading) {
    var btn = document.getElementById("trello-generate-token-btn");
    var label = document.getElementById("trello-generate-btn-label");
    var spinner = document.getElementById("trello-generate-btn-spinner");
    if (!btn) return;
    btn.dataset.loading = loading ? "true" : "false";
    btn.disabled = loading;
    if (label) label.hidden = loading;
    if (spinner) spinner.hidden = !loading;
    if (!loading) {
      syncTrelloGenerateTokenState();
    }
  }

  var trelloTokenSyncTimer = null;

  function syncTokenStatusWithRetry(projectId, maxAttempts, delayMs) {
    if (!projectId) return;
    if (trelloTokenSyncTimer) {
      clearTimeout(trelloTokenSyncTimer);
      trelloTokenSyncTimer = null;
    }

    var attempts = 0;
    var limit = maxAttempts || 8;
    var delay = delayMs || 500;

    function attemptSync() {
      attempts += 1;
      checkProjectTokenStatus(projectId).then(function (isValid) {
        if (isValid) return;
        if (attempts >= limit) return;
        trelloTokenSyncTimer = setTimeout(attemptSync, delay);
      });
    }

    attemptSync();
  }

  document.body.addEventListener("click", function (e) {
    if (!e.target.closest("#trello-generate-token-btn")) return;
    e.preventDefault();

    var projectId = getTrelloProjectId();
    var appNameEl = document.getElementById("trello-app-name");
    var appName = appNameEl ? appNameEl.value.trim() : "";
    var secretKey = getTrelloSecretKey();
    if (!projectId) { alert("Save the configuration first to generate a token."); return; }
    if (!appName) { alert("Enter Trello App Name before generating a token."); return; }
    if (!secretKey) { alert("Enter the Secret Key in the header before generating a token."); return; }

    _setTokenBtnLoading(true);

    fetch("/trello/project/" + encodeURIComponent(projectId) + "/auth-url/", {
      headers: { "X-App-Secret-Key": secretKey }
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.error) { _setTokenBtnLoading(false); alert(data.error); return; }
      var popup = window.open(data.url, "TrelloAuth", "width=600,height=700");
      if (!popup) {
        _setTokenBtnLoading(false);
        alert("Popup blocked \u2014 please allow popups for this page and try again.");
        return;
      }
      // Poll until popup closes, then refresh status
      var poll = setInterval(function () {
        try {
          if (popup.closed) {
            clearInterval(poll);
            syncTokenStatusWithRetry(projectId, 8, 500);
          }
        } catch (ex) { /* cross-origin, ignore */ }
      }, 500);
    })
    .catch(function (err) { _setTokenBtnLoading(false); alert("Failed to start Trello auth: " + err); });
  });

  function checkProjectTokenStatus(projectId) {
    return fetch("/trello/project/" + encodeURIComponent(projectId) + "/token-status/", {
      headers: { "X-App-Secret-Key": getTrelloSecretKey() }
    })
    .then(function (r) {
      return r.json().then(function (data) {
        return { ok: r.ok, data: data };
      });
    })
    .then(function (res) {
      var data = res.data || {};
      _setTokenBtnLoading(false);
      var display = document.getElementById("trello-token-display");
      var genAt = document.getElementById("trello-token-generated-at");
      var cascadeSection = document.getElementById("trello-cascade-section");

      if (!res.ok || data.error) {
        return false;
      }

      if (data.valid) {
        if (display) display.value = "••••••••";
        if (genAt) genAt.textContent = "Generated: " + data.token_generated_at;
        // Keep hidden token fields in sync with display state.
        var tokenHidden = document.querySelector("input[name='integrations[trello][token]']");
        if (tokenHidden) tokenHidden.value = "••••••••";
        var tokenAtHidden = document.querySelector("input[name='integrations[trello][token_generated_at]']");
        if (tokenAtHidden) tokenAtHidden.value = data.token_generated_at;
        // Show cascade section and load workspaces
        if (cascadeSection) {
          cascadeSection.hidden = false;
          maybeLoadTrelloCascadeForCurrentProject(true);
        }
        return true;
      } else {
        if (display) display.value = "Not generated";
        if (genAt) genAt.textContent = "";
        var tokenHiddenClear = document.querySelector("input[name='integrations[trello][token]']");
        if (tokenHiddenClear) tokenHiddenClear.value = "";
        if (cascadeSection) cascadeSection.hidden = true;
        return false;
      }
    })
    .catch(function () {
      _setTokenBtnLoading(false);
      return false;
    });
  }

  // --- Cascade dropdowns ---

  function isConfigEditMode() {
    var form = document.querySelector("form.config-form");
    return !!(form && !document.getElementById("config-form-create"));
  }

  function maybeLoadTrelloCascadeForCurrentProject(forceReload) {
    if (!isConfigEditMode()) return;

    var projectId = getTrelloProjectId();
    var cascadeSection = document.getElementById("trello-cascade-section");
    var secretKey = getTrelloSecretKey();
    var select = document.getElementById("trello-workspace-select");
    if (!projectId || !cascadeSection || cascadeSection.hidden || !select || !secretKey) return;

    if (!forceReload && select.dataset.loadedForProjectId === projectId) return;
    loadTrelloWorkspaces(projectId, !!forceReload);
  }

  function loadTrelloWorkspaces(projectId, forceReload) {
    var select = document.getElementById("trello-workspace-select");
    if (!select) return;

    if (forceReload) {
      select.dataset.loadedForProjectId = "";
    }

    var savedId = document.getElementById("trello-default-workspace-id");
    var savedVal = savedId ? savedId.value : "";

    fetch("/trello/project/" + encodeURIComponent(projectId) + "/workspaces/", {
      headers: { "X-App-Secret-Key": getTrelloSecretKey() }
    })
    .then(function (r) {
      return r.json().then(function (data) {
        return { ok: r.ok, status: r.status, data: data };
      });
    })
    .then(function (res) {
      var data = res.data;
      if (!res.ok || data.error) {
        if (res.status === 401 || res.status === 403) {
          select.innerHTML = '<option value="">— Enter valid Secret Key to load workspaces —</option>';
        } else {
          select.innerHTML = '<option value="">— Unable to load workspaces —</option>';
        }
        select.dataset.loadedForProjectId = "";
        return;
      }
      var html = '<option value="">— Select workspace —</option>';
      (Array.isArray(data) ? data : []).forEach(function (ws) {
        var sel = ws.id === savedVal ? " selected" : "";
        html += '<option value="' + ws.id + '"' + sel + '>' + (ws.displayName || ws.name || ws.id) + '</option>';
      });
      select.innerHTML = html;
      select.dataset.loadedForProjectId = projectId;
      // If a saved workspace was selected, trigger board load
      if (savedVal && select.value === savedVal) {
        syncWorkspaceHiddenFields(select);
        loadTrelloBoards(projectId, savedVal);
      }
    })
    .catch(function () {
      select.innerHTML = '<option value="">— Unable to load workspaces —</option>';
      select.dataset.loadedForProjectId = "";
    });
  }

  function syncWorkspaceHiddenFields(select) {
    var opt = select.options[select.selectedIndex];
    var idField = document.getElementById("trello-default-workspace-id");
    var nameField = document.getElementById("trello-default-workspace-name");
    if (idField) idField.value = opt ? opt.value : "";
    if (nameField) nameField.value = opt ? opt.textContent : "";
  }

  function loadTrelloBoards(projectId, workspaceId) {
    var select = document.getElementById("trello-board-select");
    if (!select) return;
    select.disabled = true;

    var savedId = document.getElementById("trello-default-board-id");
    var savedVal = savedId ? savedId.value : "";

    var url = "/trello/project/" + encodeURIComponent(projectId) + "/boards/";
    if (workspaceId) url += "?workspace=" + encodeURIComponent(workspaceId);

    fetch(url, { headers: { "X-App-Secret-Key": getTrelloSecretKey() } })
    .then(function (r) {
      return r.json().then(function (data) {
        return { ok: r.ok, status: r.status, data: data };
      });
    })
    .then(function (res) {
      var data = res.data;
      if (!res.ok || data.error) {
        if (res.status === 401 || res.status === 403) {
          select.innerHTML = '<option value="">— Unauthorized: enter valid Secret Key —</option>';
        } else {
          select.innerHTML = '<option value="">— Unable to load boards —</option>';
        }
        select.disabled = false;
        return;
      }
      var html = '<option value="">— Select board —</option>';
      html += '<option value="__create_new__">➕ Create New Board</option>';
      (Array.isArray(data) ? data : []).forEach(function (b) {
        var sel = b.id === savedVal ? " selected" : "";
        html += '<option value="' + b.id + '"' + sel + '>' + (b.name || b.id) + '</option>';
      });
      select.innerHTML = html;
      select.disabled = false;
      // If a saved board was selected, trigger list load
      if (savedVal && select.value === savedVal) {
        syncBoardHiddenFields(select);
        loadTrelloLists(projectId, savedVal);
      }
    })
    .catch(function () {
      select.innerHTML = '<option value="">— Unable to load boards —</option>';
      select.disabled = false;
    });
  }

  function syncBoardHiddenFields(select) {
    var opt = select.options[select.selectedIndex];
    var idField = document.getElementById("trello-default-board-id");
    var nameField = document.getElementById("trello-default-board-name");
    if (idField) idField.value = (opt && opt.value !== "__create_new__") ? opt.value : "";
    if (nameField) nameField.value = (opt && opt.value !== "__create_new__") ? opt.textContent : "";
  }

  function loadTrelloLists(projectId, boardId) {
    var select = document.getElementById("trello-list-select");
    if (!select) return;
    select.disabled = true;

    var savedId = document.getElementById("trello-default-list-id");
    var savedVal = savedId ? savedId.value : "";

    fetch("/trello/project/" + encodeURIComponent(projectId) + "/lists/?board=" + encodeURIComponent(boardId), {
      headers: { "X-App-Secret-Key": getTrelloSecretKey() }
    })
    .then(function (r) {
      return r.json().then(function (data) {
        return { ok: r.ok, status: r.status, data: data };
      });
    })
    .then(function (res) {
      var data = res.data;
      if (!res.ok || data.error) {
        if (res.status === 401 || res.status === 403) {
          select.innerHTML = '<option value="">— Unauthorized: enter valid Secret Key —</option>';
        } else {
          select.innerHTML = '<option value="">— Unable to load lists —</option>';
        }
        select.disabled = false;
        return;
      }
      var html = '<option value="">— Select list —</option>';
      html += '<option value="__create_new__">➕ Create New List</option>';
      (Array.isArray(data) ? data : []).forEach(function (l) {
        var sel = l.id === savedVal ? " selected" : "";
        html += '<option value="' + l.id + '"' + sel + '>' + (l.name || l.id) + '</option>';
      });
      select.innerHTML = html;
      select.disabled = false;
      if (savedVal && select.value === savedVal) {
        syncListHiddenFields(select);
      }
    })
    .catch(function () {
      select.innerHTML = '<option value="">— Unable to load lists —</option>';
      select.disabled = false;
    });
  }

  function syncListHiddenFields(select) {
    var opt = select.options[select.selectedIndex];
    var idField = document.getElementById("trello-default-list-id");
    var nameField = document.getElementById("trello-default-list-name");
    if (idField) idField.value = (opt && opt.value !== "__create_new__") ? opt.value : "";
    if (nameField) nameField.value = (opt && opt.value !== "__create_new__") ? opt.textContent : "";
  }

  // --- Cascade change handlers ---

  document.body.addEventListener("change", function (e) {
    var projectId = getTrelloProjectId();
    if (!projectId) return;

    if (e.target.id === "trello-workspace-select") {
      syncWorkspaceHiddenFields(e.target);
      var wsId = e.target.value;
      // Reset board & list
      var boardSelect = document.getElementById("trello-board-select");
      var listSelect = document.getElementById("trello-list-select");
      if (boardSelect) { boardSelect.innerHTML = '<option value="">—</option>'; boardSelect.disabled = true; }
      if (listSelect) { listSelect.innerHTML = '<option value="">—</option>'; listSelect.disabled = true; }
      document.getElementById("trello-default-board-id").value = "";
      document.getElementById("trello-default-board-name").value = "";
      document.getElementById("trello-default-list-id").value = "";
      document.getElementById("trello-default-list-name").value = "";
      if (wsId) loadTrelloBoards(projectId, wsId);
    }

    if (e.target.id === "trello-board-select") {
      if (e.target.value === "__create_new__") {
        openTrelloCreateModal("board");
        return;
      }
      syncBoardHiddenFields(e.target);
      var boardId = e.target.value;
      // Reset list
      var listSelect2 = document.getElementById("trello-list-select");
      if (listSelect2) { listSelect2.innerHTML = '<option value="">—</option>'; listSelect2.disabled = true; }
      document.getElementById("trello-default-list-id").value = "";
      document.getElementById("trello-default-list-name").value = "";
      if (boardId) loadTrelloLists(projectId, boardId);
    }

    if (e.target.id === "trello-list-select") {
      if (e.target.value === "__create_new__") {
        openTrelloCreateModal("list");
        return;
      }
      syncListHiddenFields(e.target);
    }
  });

  // --- Create New modal ---

  var trelloCreateType = "";  // "board" or "list"

  function openTrelloCreateModal(type) {
    trelloCreateType = type;
    var modal = document.getElementById("trello-create-modal");
    var title = document.getElementById("trello-create-modal-title");
    var input = document.getElementById("trello-create-modal-input");
    if (!modal) return;
    if (title) title.textContent = type === "board" ? "Create New Board" : "Create New List";
    if (input) input.value = "";
    modal.hidden = false;
    if (input) input.focus();
  }

  function closeTrelloCreateModal() {
    var modal = document.getElementById("trello-create-modal");
    if (modal) modal.hidden = true;
    // Reset the select that triggered it
    var selectId = trelloCreateType === "board" ? "trello-board-select" : "trello-list-select";
    var sel = document.getElementById(selectId);
    if (sel) sel.value = "";
    trelloCreateType = "";
  }

  document.body.addEventListener("click", function (e) {
    if (e.target.id === "trello-create-modal-cancel" || e.target.id === "trello-create-modal-overlay") {
      closeTrelloCreateModal();
    }

    if (e.target.id === "trello-create-modal-confirm") {
      var name = (document.getElementById("trello-create-modal-input").value || "").trim();
      if (!name) { alert("Enter a name."); return; }

      var projectId = getTrelloProjectId();
      if (!projectId) return;

      if (trelloCreateType === "board") {
        var wsSelect = document.getElementById("trello-workspace-select");
        var wsId = wsSelect ? wsSelect.value : "";
        fetch("/trello/project/" + encodeURIComponent(projectId) + "/create-board/", {
          method: "POST",
          headers: trelloHeaders(),
          body: JSON.stringify({ name: name, workspace_id: wsId || null })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { alert(data.error); return; }
          closeTrelloCreateModal();
          // Reload boards and select the new one
          document.getElementById("trello-default-board-id").value = data.id;
          document.getElementById("trello-default-board-name").value = data.name || name;
          loadTrelloBoards(projectId, wsId);
        })
        .catch(function (err) { alert("Failed: " + err); });

      } else if (trelloCreateType === "list") {
        var boardSelect = document.getElementById("trello-board-select");
        var boardId = document.getElementById("trello-default-board-id").value;
        if (!boardId) { alert("Select a board first."); return; }
        fetch("/trello/project/" + encodeURIComponent(projectId) + "/create-list/", {
          method: "POST",
          headers: trelloHeaders(),
          body: JSON.stringify({ name: name, board_id: boardId })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { alert(data.error); return; }
          closeTrelloCreateModal();
          // Reload lists and select the new one
          document.getElementById("trello-default-list-id").value = data.id;
          document.getElementById("trello-default-list-name").value = data.name || name;
          loadTrelloLists(projectId, boardId);
        })
        .catch(function (err) { alert("Failed: " + err); });
      }
    }
  });

  // Listen for postMessage from callback popup — update display immediately
  window.addEventListener("message", function (e) {
    if (e.origin !== window.location.origin) return;
    if (e.data === "trello_token_stored") {
      var projectId = getTrelloProjectId();
      if (projectId) syncTokenStatusWithRetry(projectId, 8, 500);
    }
  });

  // Load cascade dropdowns on page load if token exists
  (function initTrelloCascade() {
    maybeLoadTrelloCascadeForCurrentProject();
  })();

  // =========================================================================
  // Chat UI — home page interactions
  // =========================================================================

  // -----------------------------------------------------------------------
  // Agent system-prompt modal
  // -----------------------------------------------------------------------
  var agentPromptModal = document.getElementById("agent-prompt-modal");
  var agentModalTitle  = document.getElementById("agent-modal-title");
  var agentModalBody   = document.getElementById("agent-modal-body");
  var agentModalClose  = document.getElementById("agent-modal-close-btn");
  var agentModalOverlay = document.getElementById("agent-modal-overlay");

  function openAgentModal(name, systemPrompt) {
    if (!agentPromptModal) return;
    if (agentModalTitle) agentModalTitle.textContent = name + " — System Prompt";
    if (agentModalBody) {
      agentModalBody.innerHTML =
        (typeof marked !== "undefined")
          ? marked.parse(systemPrompt)
          : "<pre>" + systemPrompt.replace(/</g, "&lt;") + "</pre>";
    }
    agentPromptModal.hidden = false;
  }

  function closeAgentModal() {
    if (agentPromptModal) agentPromptModal.hidden = true;
  }

  if (agentModalClose)  agentModalClose.addEventListener("click", closeAgentModal);
  if (agentModalOverlay) agentModalOverlay.addEventListener("click", closeAgentModal);

  document.body.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      if (agentPromptModal && !agentPromptModal.hidden) closeAgentModal();
      if (editSessionModal && !editSessionModal.hidden) closeEditModal();
    }
  });

  document.body.addEventListener("click", function (e) {
    var card = e.target.closest(".project-ctx__agent-card--clickable");
    if (!card) return;
    var name = card.dataset.agentName || "Agent";
    var prompt = card.dataset.systemPrompt || "";
    openAgentModal(name, prompt);
  });

  document.body.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var card = e.target.closest(".project-ctx__agent-card--clickable");
    if (!card) return;
    e.preventDefault();
    var name = card.dataset.agentName || "Agent";
    var prompt = card.dataset.systemPrompt || "";
    openAgentModal(name, prompt);
  });

  var chatMessages = document.getElementById("chat-messages");
  var chatInput = document.getElementById("chat-input");
  var chatSendBtn = document.getElementById("chat-send-btn");
  var chatStopBtn = document.getElementById("chat-stop-btn");
  var chatProjectBtn = document.getElementById("chat-project-btn");
  var activeProjectIdInput = document.getElementById("active-project-id");
  var activeSessionIdInput = document.getElementById("active-session-id");
  var csrfToken = (document.getElementById("csrf-token-value") || {}).value || "";

  // Edit-session modal elements
  var editSessionModal = document.getElementById("edit-session-modal");
  var editModalSessionId = document.getElementById("edit-modal-session-id");
  var editSessionDescription = document.getElementById("edit-session-description");
  var editDescCharCount = document.getElementById("edit-desc-char-count");

  // Only wire up when chat elements exist (home page only)
  if (!chatMessages || !chatInput) return;

  // -----------------------------------------------------------------------
  // Auth state: show/hide chat controls based on secret key
  // -----------------------------------------------------------------------
  function updateChatAuthState() {
    var keyInput = getSecretKeyInput();
    var hasSecret = !!(keyInput && keyInput.value.trim());

    document.querySelectorAll(".chat-session-item__delete").forEach(function (btn) {
      btn.hidden = !hasSecret;
    });

    document.querySelectorAll(".chat-session-item__edit").forEach(function (btn) {
      btn.hidden = !hasSecret;
    });

    if (chatSendBtn) {
      chatSendBtn.disabled = !hasSecret;
      chatSendBtn.title = hasSecret ? "Send" : "Enter the Secret Key to send messages.";
    }
    if (chatInput) {
      chatInput.disabled = !hasSecret;
      chatInput.placeholder = hasSecret ? "Send a message" : "Enter the Secret Key to send messages.";
    }

    // If key is removed while modal is open, close it to prevent submission
    if (!hasSecret && editSessionModal && !editSessionModal.hidden) {
      closeEditModal();
    }
  }

  // -----------------------------------------------------------------------
  // Edit session modal — open / close
  // -----------------------------------------------------------------------
  function openEditModal(sessionId, description) {
    if (!editSessionModal) return;
    if (editModalSessionId) editModalSessionId.value = sessionId;
    if (editSessionDescription) {
      editSessionDescription.value = description || "";
      editSessionDescription.focus();
    }
    if (editDescCharCount) editDescCharCount.textContent = (description || "").length;
    // Set HTMX post URL dynamically
    var form = document.getElementById("edit-session-form");
    if (form) form.setAttribute("hx-post", "/chat/sessions/" + sessionId + "/update/");
    if (typeof htmx !== "undefined" && form) htmx.process(form);
    editSessionModal.hidden = false;
  }

  function closeEditModal() {
    if (editSessionModal) editSessionModal.hidden = true;
  }

  // Edit button click — event delegation on session list
  document.body.addEventListener("click", function (e) {
    var editBtn = e.target.closest(".chat-session-item__edit");
    if (!editBtn) return;
    var sessionId = editBtn.dataset.sessionId || "";
    var description = editBtn.dataset.description || "";
    if (!sessionId) return;
    openEditModal(sessionId, description);
  });

  var editModalCloseBtn = document.getElementById("edit-modal-close-btn");
  var editModalCancelBtn = document.getElementById("edit-modal-cancel-btn");
  var editModalOverlay = document.getElementById("edit-modal-overlay");

  if (editModalCloseBtn) editModalCloseBtn.addEventListener("click", closeEditModal);
  if (editModalCancelBtn) editModalCancelBtn.addEventListener("click", closeEditModal);
  if (editModalOverlay) editModalOverlay.addEventListener("click", closeEditModal);

  // Allow HTMX to swap 4xx error responses into #edit-session-form-feedback
  document.body.addEventListener("htmx:beforeSwap", function (e) {
    if (e.detail.target && e.detail.target.id === "edit-session-form-feedback") {
      if (e.detail.xhr.status === 400 || e.detail.xhr.status === 403) {
        e.detail.shouldSwap = true;
        e.detail.isError = false;
      }
    }
  });

  // Close edit modal when server signals success
  document.body.addEventListener("chatSessionUpdated", function () {
    closeEditModal();
  });

  // -----------------------------------------------------------------------
  // Edit modal description char counter
  // -----------------------------------------------------------------------
  if (editSessionDescription && editDescCharCount) {
    editSessionDescription.addEventListener("input", function () {
      editDescCharCount.textContent = editSessionDescription.value.length;
    });
  }

  // -----------------------------------------------------------------------
  // Auto-resize chat textarea
  // -----------------------------------------------------------------------
  chatInput.addEventListener("input", function () {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
  });

  // -----------------------------------------------------------------------
  // Send on Enter (Shift+Enter = newline)
  // -----------------------------------------------------------------------
  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (chatSendBtn && !chatSendBtn.disabled) chatSendBtn.click();
    }
  });

  // -----------------------------------------------------------------------
  // SSE run client
  // -----------------------------------------------------------------------

  var _activeReader = null; // ReadableStreamDefaultReader during a run

  function setRunningState(running) {
    if (chatInput)   { chatInput.disabled = running; }
    if (chatSendBtn) { chatSendBtn.hidden = running; }
    if (chatStopBtn) { chatStopBtn.hidden = !running; }
  }

  function appendBubble(html) {
    var msgs = document.getElementById("chat-history-msgs");
    if (!msgs) {
      // First message — replace the welcome block
      chatMessages.innerHTML = '<div class="chat-history" id="chat-history-msgs"></div>';
      msgs = document.getElementById("chat-history-msgs");
    }
    msgs.insertAdjacentHTML("beforeend", html);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function appendHumanBubble(text) {
    var ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    var contentHtml = (typeof marked !== "undefined")
      ? marked.parse(text)
      : "<p>" + text.replace(/</g, "&lt;") + "</p>";
    appendBubble(
      '<div class="chat-bubble chat-bubble--human">'
      + '<div class="chat-bubble__meta">'
      + '<span class="chat-bubble__name">You</span>'
      + '<span class="chat-bubble__time">' + ts + '</span>'
      + '</div>'
      + '<div class="chat-bubble__content">' + contentHtml + '</div>'
      + '</div>'
    );
  }

  function appendStatusBadge(type) {
    var label = type === "completed" ? "✅ Run completed" : "🛑 Run stopped";
    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-status-badge chat-status-badge--' + type + '">' + label + '</div>'
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function appendGatePanel(data) {
    var sessionId = activeSessionIdInput ? activeSessionIdInput.value : "";
    var modeHtml = data.mode === "feedback"
      ? '<textarea class="input input--textarea human-gate-panel__textarea" rows="3" placeholder="Type your feedback for the agents\u2026"></textarea>'
        + '<div class="human-gate-panel__actions">'
        + '<button class="btn btn--primary human-gate-btn human-gate-btn--feedback">\uD83D\uDCE4 Send Feedback</button>'
        + '<button class="btn btn--danger human-gate-btn human-gate-btn--stop">\uD83D\uDED1 Stop</button>'
        + '</div>'
      : '<div class="human-gate-panel__actions">'
        + '<button class="btn btn--success human-gate-btn human-gate-btn--approve">\u2705 Approve &amp; Continue</button>'
        + '<button class="btn btn--danger human-gate-btn human-gate-btn--stop">\uD83D\uDED1 Stop</button>'
        + '</div>';

    // Add export buttons to gate panel when no specific export agents are set (export on gate)
    var exportHtml = "";
    if (data.export && data.export.enabled) {
      var allOpen = (data.export.providers || []).some(function (p) {
        return !p.export_agents || !p.export_agents.length;
      });
      if (allOpen) {
        exportHtml = buildExportButtons(data.export);
      }
    }

    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="human-gate-panel" data-session-id="' + sessionId + '">'
      + '<div class="human-gate-panel__prompt">'
      + '\uD83D\uDC64 <strong>' + (data.human_name || "You") + '</strong>'
      + ' \u2014 Round ' + data.round + ' of ' + data.max_rounds + ' complete. What would you like to do?'
      + '</div>'
      + modeHtml
      + exportHtml
      + '</div>'
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function startRun(task) {
    var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
    if (!sessionId) { return; }

    var keyInput = getSecretKeyInput();
    var secretKey = keyInput ? keyInput.value.trim() : "";
    if (!secretKey) { alert("Enter the Secret Key first."); return; }

    setRunningState(true);

    var body = new URLSearchParams();
    body.append("task", task || "");

    fetch("/chat/sessions/" + sessionId + "/run/", {
      method: "POST",
      headers: {
        "X-App-Secret-Key": secretKey,
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
    }).then(function (response) {
      if (!response.ok) {
        return response.json().then(function (d) { throw new Error(d.error || "Run failed"); });
      }
      var reader = response.body.getReader();
      _activeReader = reader;
      var decoder = new TextDecoder();
      var buffer = "";

      function pump() {
        reader.read().then(function (result) {
          if (result.done) {
            _activeReader = null;
            setRunningState(false);
            return;
          }
          buffer += decoder.decode(result.value, { stream: true });
          var frames = buffer.split("\n\n");
          buffer = frames.pop(); // keep incomplete last frame
          frames.forEach(function (frame) {
            var eventMatch = frame.match(/^event: (\w+)/m);
            var dataMatch  = frame.match(/^data: (.+)/m);
            if (!eventMatch || !dataMatch) return;
            var eventName = eventMatch[1];
            var data;
            try { data = JSON.parse(dataMatch[1]); } catch (e) { return; }
            handleSSEEvent(eventName, data);
          });
          pump();
        }).catch(function () {
          _activeReader = null;
          setRunningState(false);
        });
      }
      pump();
    }).catch(function (err) {
      setRunningState(false);
      appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
    });
  }

  function buildExportButtons(exportMeta) {
    if (!exportMeta || !exportMeta.enabled || !exportMeta.providers) return "";
    var html = '<div class="export-actions">';
    exportMeta.providers.forEach(function (p) {
      html += '<button type="button" class="btn btn--sm btn--secondary export-btn" data-provider="' + p.name + '">'
        + '\uD83D\uDCE4 Export to ' + p.label + '</button> ';
    });
    html += '</div>';
    return html;
  }

  function shouldShowExport(exportMeta, agentName) {
    if (!exportMeta || !exportMeta.enabled) return false;
    var lower = (agentName || "").toLowerCase();
    return (exportMeta.providers || []).some(function (p) {
      if (!p.export_agents || !p.export_agents.length) return true;
      return p.export_agents.some(function (n) { return n.toLowerCase() === lower; });
    });
  }

  function handleSSEEvent(eventName, data) {
    if (eventName === "message") {
      var ts = data.timestamp || "";
      var initial = (data.agent_name || "A").slice(0, 1).toUpperCase();
      var contentHtml = (typeof marked !== "undefined")
        ? marked.parse(data.content || "")
        : "<p>" + (data.content || "").replace(/</g, "&lt;") + "</p>";
      var exportHtml = shouldShowExport(data.export, data.agent_name)
        ? buildExportButtons(data.export)
        : "";
      appendBubble(
        '<div class="chat-bubble chat-bubble--ai">'
        + '<div class="chat-bubble__avatar">' + initial + '</div>'
        + '<div class="chat-bubble__body">'
        + '<div class="chat-bubble__meta">'
        + '<span class="chat-bubble__name">' + (data.agent_name || "Agent") + '</span>'
        + '<span class="chat-bubble__time">' + ts + '</span>'
        + '</div>'
        + '<div class="chat-bubble__content">' + contentHtml + '</div>'
        + exportHtml
        + '</div></div>'
      );
    } else if (eventName === "gate") {
      setRunningState(false);
      appendGatePanel(data);
    } else if (eventName === "done") {
      setRunningState(false);
      appendStatusBadge("completed");
      if (data.export && data.export.enabled) {
        appendBubble(buildExportButtons(data.export));
      }
    } else if (eventName === "stopped") {
      setRunningState(false);
      appendStatusBadge("stopped");
    } else if (eventName === "error") {
      setRunningState(false);
      appendBubble('<div class="chat-bubble chat-bubble--error">\u26A0\uFE0F ' + (data.message || "Unknown error") + '</div>');
    }
  }

  // -----------------------------------------------------------------------
  // Send button — auto-creates session when none exists
  // -----------------------------------------------------------------------
  if (chatSendBtn) {
    chatSendBtn.addEventListener("click", function () {
      if (chatSendBtn.disabled) return;
      var text = chatInput.value.trim();
      if (!text) return;

      var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      if (!sessionId) {
        // No active session — auto-create one using first 150 chars as description
        var projectId = activeProjectIdInput ? activeProjectIdInput.value.trim() : "";
        if (!projectId) { alert("Select a project first."); return; }

        var keyInput = getSecretKeyInput();
        var secretKey = keyInput ? keyInput.value.trim() : "";
        if (!secretKey) { alert("Enter the Secret Key first."); return; }

        var description = text.substring(0, 150);

        // Disable input while creating
        chatSendBtn.disabled = true;
        chatInput.disabled = true;

        var body = new URLSearchParams();
        body.append("project_id", projectId);
        body.append("description", description);

        fetch("/chat/sessions/create/", {
          method: "POST",
          headers: {
            "X-App-Secret-Key": secretKey,
            "X-CSRFToken": csrfToken,
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: body.toString(),
        }).then(function (response) {
          if (!response.ok) {
            throw new Error("Failed to create session.");
          }
          return response.text();
        }).then(function (html) {
          // Let HTMX process OOB swaps from the response
          var tmp = document.createElement("div");
          tmp.innerHTML = html;
          // Process OOB swaps manually
          tmp.querySelectorAll("[hx-swap-oob]").forEach(function (el) {
            var targetId = el.id;
            var target = document.getElementById(targetId);
            if (target) {
              if (el.tagName === "INPUT") {
                // outerHTML swap for hidden inputs
                target.outerHTML = el.outerHTML;
                // Re-acquire the reference
                if (targetId === "active-session-id") {
                  activeSessionIdInput = document.getElementById("active-session-id");
                }
              } else {
                target.innerHTML = el.innerHTML;
              }
            }
          });
          updateChatAuthState();

          // Now we have a session — set up chat area and send the message
          if (chatMessages) {
            chatMessages.innerHTML = '<div class="chat-history" id="chat-history-msgs"></div>';
          }
          appendHumanBubble(text);
          chatInput.value = "";
          chatInput.style.height = "auto";
          chatInput.focus();
          startRun(text);
        }).catch(function (err) {
          appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
        }).finally(function () {
          chatSendBtn.disabled = false;
          chatInput.disabled = false;
        });
        return;
      }

      appendHumanBubble(text);
      chatInput.value = "";
      chatInput.style.height = "auto";
      chatInput.focus();
      startRun(text);
    });
  }

  // -----------------------------------------------------------------------
  // Stop button
  // -----------------------------------------------------------------------
  if (chatStopBtn) {
    chatStopBtn.addEventListener("click", function () {
      var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      var keyInput = getSecretKeyInput();
      var secretKey = keyInput ? keyInput.value.trim() : "";
      if (!sessionId || !secretKey) return;
      fetch("/chat/sessions/" + sessionId + "/stop/", {
        method: "POST",
        headers: { "X-App-Secret-Key": secretKey, "X-CSRFToken": csrfToken },
      });
      // SSE stream emits 'stopped' event which calls setRunningState(false)
    });
  }

  // -----------------------------------------------------------------------
  // Human gate panel — event delegation
  // -----------------------------------------------------------------------
  document.body.addEventListener("click", function (e) {
    var panel = e.target.closest(".human-gate-panel");
    if (!panel) return;

    var sessionId = panel.dataset.sessionId
      || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var keyInput = getSecretKeyInput();
    var secretKey = keyInput ? keyInput.value.trim() : "";
    if (!sessionId || !secretKey) return;

    function sendRespond(action, text) {
      var body = new URLSearchParams({ action: action });
      if (text) body.append("text", text);
      return fetch("/chat/sessions/" + sessionId + "/respond/", {
        method: "POST",
        headers: {
          "X-App-Secret-Key": secretKey,
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: body.toString(),
      }).then(function (r) { return r.json(); });
    }

    if (e.target.closest(".human-gate-btn--approve")) {
      panel.remove();
      sendRespond("approve", "").then(function (d) {
        if (d.status === "ok") startRun("");
      });
    } else if (e.target.closest(".human-gate-btn--feedback")) {
      var ta = panel.querySelector(".human-gate-panel__textarea");
      var text = ta ? ta.value.trim() : "";
      if (!text) { ta && ta.focus(); return; }
      panel.remove();
      var fbTs = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      var fbHtml = (typeof marked !== "undefined") ? marked.parse(text) : "<p>" + text.replace(/</g, "&lt;") + "</p>";
      appendBubble(
        '<div class="chat-bubble chat-bubble--human">'
        + '<div class="chat-bubble__meta">'
        + '<span class="chat-bubble__name">You</span>'
        + '<span class="chat-bubble__time">' + fbTs + '</span>'
        + '</div>'
        + '<div class="chat-bubble__content">' + fbHtml + '</div>'
        + '</div>'
      );
      sendRespond("feedback", text).then(function (d) {
        if (d.status === "ok") startRun(d.task || text);
      });
    } else if (e.target.closest(".human-gate-btn--stop")) {
      panel.remove();
      sendRespond("stop", "").then(function () {
        appendStatusBadge("stopped");
      });
    }
  });

  // -----------------------------------------------------------------------
  // Project selection from chat panel dropdown
  // -----------------------------------------------------------------------
  document.body.addEventListener("click", function (e) {
    var item = e.target.closest(".chat-project-item");
    if (!item) return;

    e.preventDefault();
    var projectName = item.dataset.project;
    var projectId = item.dataset.projectId;
    if (!projectName) return;

    // Update dropdown button label
    if (chatProjectBtn) {
      chatProjectBtn.textContent = projectName + " \u25BE";
      chatProjectBtn.dataset.activeProject = projectName;
      chatProjectBtn.dataset.activeProjectId = projectId;
    }

    // Track active project for modal; clear active session
    if (activeProjectIdInput) activeProjectIdInput.value = projectId || "";
    if (activeSessionIdInput) activeSessionIdInput.value = "";
  });

  // -----------------------------------------------------------------------
  // Session selection — set activeSessionIdInput when an HTMX session link fires
  // -----------------------------------------------------------------------
  document.body.addEventListener("htmx:beforeRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (!elt) return;
    var li = elt.closest("li[data-session-id]");
    if (li && activeSessionIdInput) activeSessionIdInput.value = li.dataset.sessionId || "";
  });

  // Show/hide edit/delete buttons after HTMX swaps new session list items
  document.body.addEventListener("htmx:afterSwap", function () {
    updateChatAuthState();
  });

  // Also update on secret key input
  document.body.addEventListener("input", function (e) {
    if (e.target.id === "global-secret-key") {
      updateChatAuthState();
    }
  });

  updateChatAuthState();

  // -----------------------------------------------------------------------
  // Export — delegate to TrelloExport modal (trello.js)
  // -----------------------------------------------------------------------
  document.body.addEventListener("click", function (e) {
    var btn = e.target.closest(".export-btn");
    if (!btn) return;

    var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
    var keyInput = getSecretKeyInput();
    var secretKey = keyInput ? keyInput.value.trim() : "";
    if (!sessionId || !secretKey) { alert("Enter the Secret Key first."); return; }

    var provider = btn.dataset.provider;
    if (provider === "trello" && window.TrelloExport) {
      window.TrelloExport.openModal(sessionId, secretKey, csrfToken);
    }
  });

});
