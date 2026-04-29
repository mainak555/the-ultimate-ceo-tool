/**
 * trello_config.js - Trello configuration interactions for the project config form.
 *
 * Scope:
 *   - Config page only (create/edit project form)
 *   - Token generation flow
 *   - Workspace -> board -> list cascade defaults
 *   - Inline create board/list modal
 */

(function () {
  "use strict";

  function getSecretKey() {
    var input = document.getElementById("global-secret-key");
    return input ? input.value.trim() : "";
  }

  function getProjectId() {
    var el = document.getElementById("config-project-id");
    return el ? el.value.trim() : "";
  }

  function isConfigPage() {
    return !!document.querySelector("form.config-form");
  }

  function isConfigEditMode() {
    var form = document.querySelector("form.config-form");
    return !!(form && !document.getElementById("config-form-create"));
  }

  function headersJson() {
    var csrfInput = document.querySelector("[name=csrfmiddlewaretoken]");
    return {
      "Content-Type": "application/json",
      "X-App-Secret-Key": getSecretKey(),
      "X-CSRFToken": csrfInput ? csrfInput.value : ""
    };
  }

  function syncTrelloToggleFields() {
    var integrationsEnabled = document.getElementById("integrations-enabled");
    var trelloEnabled = document.getElementById("integrations-trello-enabled");
    var trelloFields = document.getElementById("integrations-trello-fields");
    if (!trelloFields) return;

    var integrationsOn = !integrationsEnabled || integrationsEnabled.checked;
    var trelloOn = integrationsOn && !!(trelloEnabled && trelloEnabled.checked);

    trelloFields.hidden = !trelloOn;
    trelloFields.querySelectorAll("input, select, textarea").forEach(function (field) {
      if (field.id === "trello-token-display") return;
      field.disabled = !trelloOn;
    });
  }

  function syncGenerateTokenState() {
    var button = document.getElementById("trello-generate-token-btn");
    if (!button) return;

    if (button.dataset.loading === "true") return;

    var isCreateMode = !!document.getElementById("config-form-create");
    var projectId = getProjectId();
    var appNameEl = document.getElementById("trello-app-name");
    var appName = appNameEl ? appNameEl.value.trim() : "";
    var hasSecret = !!getSecretKey();

    var integrationsEnabled = document.getElementById("integrations-enabled");
    var trelloEnabled = document.getElementById("integrations-trello-enabled");
    var trelloOn = !!(
      (!integrationsEnabled || integrationsEnabled.checked) &&
      (!trelloEnabled || trelloEnabled.checked)
    );

    var canGenerate = !isCreateMode && !!projectId && !!appName && hasSecret && trelloOn;
    button.disabled = !canGenerate;

    if (isCreateMode || !projectId) {
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
    if (!trelloOn) {
      button.title = "Enable Trello integration to generate a token.";
      return;
    }
    button.title = "";
  }

  function setTokenBtnLoading(loading) {
    var btn = document.getElementById("trello-generate-token-btn");
    var label = document.getElementById("trello-generate-btn-label");
    var spinner = document.getElementById("trello-generate-btn-spinner");
    if (!btn) return;

    btn.dataset.loading = loading ? "true" : "false";
    btn.disabled = loading;
    if (label) label.hidden = loading;
    if (spinner) spinner.hidden = !loading;

    if (!loading) syncGenerateTokenState();
  }

  var tokenSyncTimer = null;

  function syncTokenStatusWithRetry(projectId, maxAttempts, delayMs) {
    if (!projectId) return;

    if (tokenSyncTimer) {
      clearTimeout(tokenSyncTimer);
      tokenSyncTimer = null;
    }

    var attempts = 0;
    var limit = maxAttempts || 8;
    var delay = delayMs || 500;

    function attemptSync() {
      attempts += 1;
      checkProjectTokenStatus(projectId).then(function (isValid) {
        if (isValid) return;
        if (attempts >= limit) return;
        tokenSyncTimer = setTimeout(attemptSync, delay);
      });
    }

    attemptSync();
  }

  function checkProjectTokenStatus(projectId) {
    return fetch("/trello/project/" + encodeURIComponent(projectId) + "/token-status/", {
      headers: { "X-App-Secret-Key": getSecretKey() }
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        var data = res.data || {};
        setTokenBtnLoading(false);

        var display = document.getElementById("trello-token-display");
        var generatedAt = document.getElementById("trello-token-generated-at");
        var cascadeSection = document.getElementById("trello-cascade-section");

        if (!res.ok || data.error) {
          return false;
        }

        if (data.valid) {
          if (display) display.value = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
          if (generatedAt) {
            generatedAt.innerHTML = 'Generated: <time class="local-time" data-utc="' + data.token_generated_at + '">' + data.token_generated_at + '</time>';
            if (window.renderLocalTimes) window.renderLocalTimes();
          }

          var tokenHidden = document.querySelector("input[name='integrations[trello][token]']");
          if (tokenHidden) tokenHidden.value = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
          var tokenAtHidden = document.querySelector("input[name='integrations[trello][token_generated_at]']");
          if (tokenAtHidden) tokenAtHidden.value = data.token_generated_at;

          if (cascadeSection) {
            cascadeSection.hidden = false;
            maybeLoadCascadeForCurrentProject(true);
          }
          return true;
        }

        if (display) display.value = "Not generated";
        if (generatedAt) generatedAt.textContent = "";

        var tokenHiddenClear = document.querySelector("input[name='integrations[trello][token]']");
        if (tokenHiddenClear) tokenHiddenClear.value = "";
        if (cascadeSection) cascadeSection.hidden = true;
        return false;
      })
      .catch(function () {
        setTokenBtnLoading(false);
        return false;
      });
  }

  function maybeLoadCascadeForCurrentProject(forceReload) {
    if (!isConfigEditMode()) return;

    var projectId = getProjectId();
    var cascadeSection = document.getElementById("trello-cascade-section");
    var select = document.getElementById("trello-workspace-select");
    if (!projectId || !cascadeSection || cascadeSection.hidden || !select || !getSecretKey()) return;

    if (!forceReload && select.dataset.loadedForProjectId === projectId) return;
    loadWorkspaces(projectId, !!forceReload);
  }

  function loadWorkspaces(projectId, forceReload) {
    var select = document.getElementById("trello-workspace-select");
    if (!select) return;

    if (forceReload) select.dataset.loadedForProjectId = "";

    var savedIdField = document.getElementById("trello-default-workspace-id");
    var savedId = savedIdField ? savedIdField.value : "";

    fetch("/trello/project/" + encodeURIComponent(projectId) + "/workspaces/", {
      headers: { "X-App-Secret-Key": getSecretKey() }
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
            select.innerHTML = '<option value="">\u2014 Enter valid Secret Key to load workspaces \u2014</option>';
          } else {
            select.innerHTML = '<option value="">\u2014 Unable to load workspaces \u2014</option>';
          }
          select.dataset.loadedForProjectId = "";
          return;
        }

        var html = '<option value="">\u2014 Select workspace \u2014</option>';
        (Array.isArray(data) ? data : []).forEach(function (ws) {
          var selected = ws.id === savedId ? " selected" : "";
          html += '<option value="' + ws.id + '"' + selected + '>' + (ws.displayName || ws.name || ws.id) + '</option>';
        });
        select.innerHTML = html;
        select.dataset.loadedForProjectId = projectId;

        if (savedId && select.value === savedId) {
          syncWorkspaceHiddenFields(select);
          loadBoards(projectId, savedId);
        }
      })
      .catch(function () {
        select.innerHTML = '<option value="">\u2014 Unable to load workspaces \u2014</option>';
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

  function loadBoards(projectId, workspaceId) {
    var select = document.getElementById("trello-board-select");
    if (!select) return;

    select.disabled = true;

    var savedIdField = document.getElementById("trello-default-board-id");
    var savedId = savedIdField ? savedIdField.value : "";

    var url = "/trello/project/" + encodeURIComponent(projectId) + "/boards/";
    if (workspaceId) url += "?workspace=" + encodeURIComponent(workspaceId);

    fetch(url, { headers: { "X-App-Secret-Key": getSecretKey() } })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, status: r.status, data: data };
        });
      })
      .then(function (res) {
        var data = res.data;
        if (!res.ok || data.error) {
          if (res.status === 401 || res.status === 403) {
            select.innerHTML = '<option value="">\u2014 Unauthorized: enter valid Secret Key \u2014</option>';
          } else {
            select.innerHTML = '<option value="">\u2014 Unable to load boards \u2014</option>';
          }
          select.disabled = false;
          return;
        }

        var html = '<option value="">\u2014 Select board \u2014</option>';
        html += '<option value="__create_new__">\u2795 Create New Board</option>';
        (Array.isArray(data) ? data : []).forEach(function (board) {
          var selected = board.id === savedId ? " selected" : "";
          html += '<option value="' + board.id + '"' + selected + '>' + (board.name || board.id) + '</option>';
        });
        select.innerHTML = html;
        select.disabled = false;

        if (savedId && select.value === savedId) {
          syncBoardHiddenFields(select);
          loadLists(projectId, savedId);
        }
      })
      .catch(function () {
        select.innerHTML = '<option value="">\u2014 Unable to load boards \u2014</option>';
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

  function loadLists(projectId, boardId) {
    var select = document.getElementById("trello-list-select");
    if (!select) return;

    select.disabled = true;

    var savedIdField = document.getElementById("trello-default-list-id");
    var savedId = savedIdField ? savedIdField.value : "";

    fetch("/trello/project/" + encodeURIComponent(projectId) + "/lists/?board=" + encodeURIComponent(boardId), {
      headers: { "X-App-Secret-Key": getSecretKey() }
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
            select.innerHTML = '<option value="">\u2014 Unauthorized: enter valid Secret Key \u2014</option>';
          } else {
            select.innerHTML = '<option value="">\u2014 Unable to load lists \u2014</option>';
          }
          select.disabled = false;
          return;
        }

        var html = '<option value="">\u2014 Select list \u2014</option>';
        html += '<option value="__create_new__">\u2795 Create New List</option>';
        (Array.isArray(data) ? data : []).forEach(function (list) {
          var selected = list.id === savedId ? " selected" : "";
          html += '<option value="' + list.id + '"' + selected + '>' + (list.name || list.id) + '</option>';
        });
        select.innerHTML = html;
        select.disabled = false;

        if (savedId && select.value === savedId) {
          syncListHiddenFields(select);
        }
      })
      .catch(function () {
        select.innerHTML = '<option value="">\u2014 Unable to load lists \u2014</option>';
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

  var createType = "";

  function openCreateModal(type) {
    createType = type;

    var modal = document.getElementById("trello-create-modal");
    var title = document.getElementById("trello-create-modal-title");
    var input = document.getElementById("trello-create-modal-input");
    if (!modal) return;

    if (title) title.textContent = type === "board" ? "Create New Board" : "Create New List";
    if (input) input.value = "";

    modal.hidden = false;
    if (input) input.focus();
  }

  function closeCreateModal() {
    var modal = document.getElementById("trello-create-modal");
    if (modal) modal.hidden = true;

    var selectId = createType === "board" ? "trello-board-select" : "trello-list-select";
    var select = document.getElementById(selectId);
    if (select) select.value = "";

    createType = "";
  }

  function syncFromForm() {
    if (!isConfigPage()) return;
    syncTrelloToggleFields();
    syncGenerateTokenState();
    maybeLoadCascadeForCurrentProject(false);
  }

  function bindEvents() {
    document.body.addEventListener("click", function (e) {
      if (!e.target.closest("#trello-generate-token-btn")) return;
      e.preventDefault();

      var projectId = getProjectId();
      var appNameEl = document.getElementById("trello-app-name");
      var appName = appNameEl ? appNameEl.value.trim() : "";
      var secretKey = getSecretKey();

      if (!projectId) { alert("Save the configuration first to generate a token."); return; }
      if (!appName) { alert("Enter Trello App Name before generating a token."); return; }
      if (!secretKey) { alert("Enter the Secret Key in the header before generating a token."); return; }

      setTokenBtnLoading(true);

      fetch("/trello/project/" + encodeURIComponent(projectId) + "/auth-url/", {
        headers: { "X-App-Secret-Key": secretKey }
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) {
            setTokenBtnLoading(false);
            alert(data.error);
            return;
          }

          var popup = window.open(data.url, "TrelloAuth", "width=600,height=700");
          if (!popup) {
            setTokenBtnLoading(false);
            alert("Popup blocked - please allow popups for this page and try again.");
            return;
          }

          var poll = setInterval(function () {
            try {
              if (popup.closed) {
                clearInterval(poll);
                syncTokenStatusWithRetry(projectId, 8, 500);
              }
            } catch (ex) {
              // Ignore cross-origin checks while popup is open.
            }
          }, 500);
        })
        .catch(function (err) {
          setTokenBtnLoading(false);
          alert("Failed to start Trello auth: " + err);
        });
    });

    document.body.addEventListener("change", function (e) {
      var projectId = getProjectId();

      if (e.target.id === "integrations-enabled" || e.target.id === "integrations-trello-enabled") {
        syncFromForm();
      }

      if (!projectId) return;

      if (e.target.id === "trello-workspace-select") {
        syncWorkspaceHiddenFields(e.target);
        var workspaceId = e.target.value;

        var boardSelect = document.getElementById("trello-board-select");
        var listSelect = document.getElementById("trello-list-select");
        if (boardSelect) {
          boardSelect.innerHTML = '<option value="">\u2014</option>';
          boardSelect.disabled = true;
        }
        if (listSelect) {
          listSelect.innerHTML = '<option value="">\u2014</option>';
          listSelect.disabled = true;
        }

        document.getElementById("trello-default-board-id").value = "";
        document.getElementById("trello-default-board-name").value = "";
        document.getElementById("trello-default-list-id").value = "";
        document.getElementById("trello-default-list-name").value = "";

        if (workspaceId) loadBoards(projectId, workspaceId);
      }

      if (e.target.id === "trello-board-select") {
        if (e.target.value === "__create_new__") {
          openCreateModal("board");
          return;
        }

        syncBoardHiddenFields(e.target);
        var boardId = e.target.value;

        var listSelect2 = document.getElementById("trello-list-select");
        if (listSelect2) {
          listSelect2.innerHTML = '<option value="">\u2014</option>';
          listSelect2.disabled = true;
        }
        document.getElementById("trello-default-list-id").value = "";
        document.getElementById("trello-default-list-name").value = "";

        if (boardId) loadLists(projectId, boardId);
      }

      if (e.target.id === "trello-list-select") {
        if (e.target.value === "__create_new__") {
          openCreateModal("list");
          return;
        }
        syncListHiddenFields(e.target);
      }
    });

    document.body.addEventListener("click", function (e) {
      if (e.target.id === "trello-create-modal-cancel" || e.target.id === "trello-create-modal-overlay") {
        closeCreateModal();
      }

      if (e.target.id === "trello-create-modal-confirm") {
        var name = (document.getElementById("trello-create-modal-input").value || "").trim();
        if (!name) {
          alert("Enter a name.");
          return;
        }

        var projectId = getProjectId();
        if (!projectId) return;

        if (createType === "board") {
          var wsSelect = document.getElementById("trello-workspace-select");
          var workspaceId = wsSelect ? wsSelect.value : "";

          fetch("/trello/project/" + encodeURIComponent(projectId) + "/create-board/", {
            method: "POST",
            headers: headersJson(),
            body: JSON.stringify({ name: name, workspace_id: workspaceId || null })
          })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.error) {
                alert(data.error);
                return;
              }
              closeCreateModal();
              document.getElementById("trello-default-board-id").value = data.id;
              document.getElementById("trello-default-board-name").value = data.name || name;
              loadBoards(projectId, workspaceId);
            })
            .catch(function (err) {
              alert("Failed: " + err);
            });
        } else if (createType === "list") {
          var boardId = document.getElementById("trello-default-board-id").value;
          if (!boardId) {
            alert("Select a board first.");
            return;
          }

          fetch("/trello/project/" + encodeURIComponent(projectId) + "/create-list/", {
            method: "POST",
            headers: headersJson(),
            body: JSON.stringify({ name: name, board_id: boardId })
          })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.error) {
                alert(data.error);
                return;
              }
              closeCreateModal();
              document.getElementById("trello-default-list-id").value = data.id;
              document.getElementById("trello-default-list-name").value = data.name || name;
              loadLists(projectId, boardId);
            })
            .catch(function (err) {
              alert("Failed: " + err);
            });
        }
      }
    });

    window.addEventListener("message", function (e) {
      if (e.origin !== window.location.origin) return;
      if (e.data === "trello_token_stored") {
        var projectId = getProjectId();
        if (projectId) syncTokenStatusWithRetry(projectId, 8, 500);
      }
    });

    document.body.addEventListener("htmx:afterSwap", function () {
      syncFromForm();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!isConfigPage()) return;

    bindEvents();
    syncFromForm();

    window.TrelloConfig = {
      syncFromForm: syncFromForm,
      syncGenerateTokenState: syncGenerateTokenState,
      maybeLoadCascadeForCurrentProject: maybeLoadCascadeForCurrentProject
    };

    if (window.ProviderRegistry && typeof window.ProviderRegistry.register === "function") {
      window.ProviderRegistry.register("trello", {
        syncConfigState: syncFromForm,
      });
    }
  });
})();
