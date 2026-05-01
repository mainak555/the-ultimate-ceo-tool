/**
 * project_config.js - Project configuration feature behavior.
 *
 * Scope:
 *   - Config form state sync
 *   - Agent card add/remove/reindex
 *   - Human gate/team/integration field visibility
 *   - Secret-gated button states on config page + project sidebar delete controls
 */

document.addEventListener("DOMContentLoaded", function () {
  function getSecretKeyInput() {
    if (window.AppCommon && typeof window.AppCommon.getSecretKeyInput === "function") {
      return window.AppCommon.getSecretKeyInput();
    }
    return document.getElementById("global-secret-key");
  }

  function getSecretKey() {
    if (window.AppCommon && typeof window.AppCommon.getSecretKey === "function") {
      return window.AppCommon.getSecretKey();
    }
    var keyInput = getSecretKeyInput();
    return keyInput ? keyInput.value.trim() : "";
  }

  function isConfigPage() {
    return !!document.querySelector("form.config-form");
  }

  function syncProviderConfigState(providerName) {
    if (window.ProviderRegistry && typeof window.ProviderRegistry.syncConfigState === "function") {
      if (window.ProviderRegistry.syncConfigState(providerName)) return;
    }

    // Backward-compatible fallback for older provider modules.
    if (providerName === "trello" && window.TrelloConfig && typeof window.TrelloConfig.syncFromForm === "function") {
      window.TrelloConfig.syncFromForm();
    }
  }

  function updateSubmitState() {
    var hasSecret = !!getSecretKey();

    document.querySelectorAll(".config-form button[type='submit'], .config-form .js-requires-secret").forEach(function (button) {
      if (button.id === "trello-generate-token-btn") {
        return;
      }
      button.disabled = !hasSecret;
      button.title = hasSecret ? "" : "Enter the Secret Key in the header before saving.";
    });

    document.querySelectorAll(".sidebar__delete").forEach(function (btn) {
      var blocked = btn.dataset.deleteBlocked === "true";
      var defaultTitle = btn.dataset.defaultTitle || "Delete project";
      var blockedTitle = btn.dataset.blockedTitle || "Cannot delete project while chat sessions exist.";
      btn.hidden = !hasSecret;
      btn.disabled = false;
      if (blocked) {
        btn.title = blockedTitle;
      } else {
        btn.title = hasSecret ? defaultTitle : "Enter the Secret Key in the header before deleting.";
      }
    });

    syncProviderConfigState("trello");
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
  }

  function syncMcpDedicatedVisibility() {
    document.querySelectorAll(".js-mcp-tools-select").forEach(function (sel) {
      var idx = sel.getAttribute("data-agent-index");
      var card = sel.closest(".agent-card");
      if (!card) return;
      var wrap = card.querySelector(".js-mcp-dedicated-wrap[data-agent-index='" + idx + "']") ||
                 card.querySelector(".js-mcp-dedicated-wrap");
      if (!wrap) return;
      var isDedicated = sel.value === "dedicated";
      wrap.hidden = !isDedicated;
      wrap.querySelectorAll("textarea").forEach(function (ta) {
        ta.disabled = !isDedicated;
      });
    });
  }

  function tryParseJson(value, label) {
    var text = (value || "").trim();
    if (!text) return { ok: true, empty: true };
    try {
      JSON.parse(text);
      return { ok: true, empty: false };
    } catch (err) {
      return { ok: false, error: label + ": " + err.message };
    }
  }

  function validateMcpJsonOnSubmit(form) {
    var errors = [];
    form.querySelectorAll(".js-mcp-tools-select").forEach(function (sel) {
      if (sel.value !== "dedicated") return;
      var card = sel.closest(".agent-card");
      var nameInput = card ? card.querySelector("[name$='[name]']") : null;
      var agentLabel = nameInput && nameInput.value ? nameInput.value : ("agent #" + (sel.getAttribute("data-agent-index") || "?"));
      var ta = card ? card.querySelector(".js-mcp-dedicated-json") : null;
      if (!ta) return;
      var result = tryParseJson(ta.value, "Dedicated MCP for " + agentLabel);
      if (!result.ok) errors.push(result.error);
      else if (result.empty) errors.push("Dedicated MCP for " + agentLabel + ": JSON configuration is required.");
    });
    var sharedTa = form.querySelector(".js-shared-mcp-json");
    if (sharedTa) {
      var sharedResult = tryParseJson(sharedTa.value, "Shared MCP Tools");
      if (!sharedResult.ok) errors.push(sharedResult.error);
    }

    // Validate MCP secret keys: UPPER_SNAKE, unique, value present
    var keyRe = /^[A-Z][A-Z0-9_]*$/;
    var seenKeys = Object.create(null);
    var definedKeys = Object.create(null);
    form.querySelectorAll(".mcp-secrets__row").forEach(function (row) {
      var keyInput = row.querySelector(".js-mcp-secret-key");
      var valInput = row.querySelector(".js-mcp-secret-value");
      var key = keyInput ? (keyInput.value || "").trim() : "";
      var val = valInput ? valInput.value : "";
      if (!key && !val) return;
      if (!key) {
        errors.push("MCP Secrets: a row has a value but no key.");
        return;
      }
      if (!keyRe.test(key)) {
        errors.push("MCP Secrets: '" + key + "' must be UPPER_SNAKE_CASE.");
        return;
      }
      if (seenKeys[key]) {
        errors.push("MCP Secrets: duplicate key '" + key + "'.");
        return;
      }
      seenKeys[key] = true;
      if (val === "") {
        errors.push("MCP Secrets: '" + key + "' value is empty.");
        return;
      }
      definedKeys[key] = true;
    });

    // Warn on unresolved {KEY} placeholders in shared + dedicated MCP JSON
    var placeholderRe = /\{([A-Z][A-Z0-9_]*)\}/g;
    var jsonTextareas = [];
    if (sharedTa) jsonTextareas.push(sharedTa);
    form.querySelectorAll(".js-mcp-dedicated-json").forEach(function (ta) {
      jsonTextareas.push(ta);
    });
    var referenced = Object.create(null);
    jsonTextareas.forEach(function (ta) {
      var text = ta.value || "";
      var m;
      while ((m = placeholderRe.exec(text)) !== null) {
        referenced[m[1]] = true;
      }
    });
    Object.keys(referenced).forEach(function (k) {
      if (!definedKeys[k]) {
        errors.push("MCP configuration references undefined secret '{" + k + "}'.");
      }
    });

    return errors;
  }

  function listAgentNames() {
    var container = document.getElementById("agents-container");
    if (!container) return [];

    var names = [];
    container.querySelectorAll(".agent-card [name$='[name]']").forEach(function (nameInput) {
      var name = (nameInput.value || "").trim();
      if (name) names.push(name);
    });
    return names;
  }

  function getAssistantCount() {
    var container = document.getElementById("agents-container");
    if (!container) return 0;
    return container.querySelectorAll(".agent-card").length;
  }

  function syncSingleAssistantMode() {
    var assistantCount = getAssistantCount();
    var isSingleAssistant = assistantCount === 1;

    var humanGateEnabled = document.getElementById("human-gate-enabled");
    var humanGateDefaultHint = document.getElementById("human-gate-default-hint");
    var humanGateSingleHint = document.getElementById("human-gate-single-assistant-hint");
    var teamFieldset = document.getElementById("team-settings-fieldset");
    var teamDisabledHint = document.getElementById("team-settings-disabled-hint");
    var teamTypeSelect = document.getElementById("team_type");

    if (humanGateEnabled) {
      if (isSingleAssistant) {
        humanGateEnabled.checked = true;
      }
      humanGateEnabled.disabled = isSingleAssistant;

      var forcedInput = document.getElementById("human-gate-enabled-forced");
      if (isSingleAssistant && !forcedInput) {
        forcedInput = document.createElement("input");
        forcedInput.type = "hidden";
        forcedInput.id = "human-gate-enabled-forced";
        forcedInput.name = "human_gate[enabled]";
        forcedInput.value = "on";
        humanGateEnabled.insertAdjacentElement("afterend", forcedInput);
      } else if (!isSingleAssistant && forcedInput) {
        forcedInput.remove();
      }
    }

    if (humanGateDefaultHint) {
      humanGateDefaultHint.hidden = isSingleAssistant;
    }
    if (humanGateSingleHint) {
      humanGateSingleHint.hidden = !isSingleAssistant;
    }

    // Remote Users block is multi-assistant only.
    var remoteBlock = document.getElementById("human-gate-remote-block");
    if (remoteBlock) {
      remoteBlock.hidden = isSingleAssistant;
      remoteBlock.querySelectorAll("input, select, textarea").forEach(function (field) {
        if (isSingleAssistant) field.disabled = true;
      });
    }

    if (teamFieldset) {
      teamFieldset.hidden = isSingleAssistant;
      teamFieldset.querySelectorAll("input, select, textarea").forEach(function (field) {
        field.disabled = isSingleAssistant;
      });
    }
    if (teamDisabledHint) {
      teamDisabledHint.hidden = !isSingleAssistant;
    }

    if (isSingleAssistant && teamTypeSelect && teamTypeSelect.value === "selector") {
      teamTypeSelect.value = "round_robin";
    }
  }

  function syncExportAgentCheckboxes() {
    var names = listAgentNames();

    // Sync Trello export_agents checkboxes
    var trelloWrapper = document.getElementById("integrations-export-agents");
    if (trelloWrapper) {
      var trelloChecked = new Set();
      trelloWrapper.querySelectorAll("input[name='integrations[trello][export_agents]']:checked").forEach(function (checkbox) {
        trelloChecked.add(checkbox.value);
      });
      var trelloHtml = "";
      names.forEach(function (name) {
        var checked = trelloChecked.has(name) ? " checked" : "";
        trelloHtml += '<div class="form-group form-group--inline">';
        trelloHtml += '<label>';
        trelloHtml += '<input type="checkbox" name="integrations[trello][export_agents]" value="' + name + '"' + checked + '>';
        trelloHtml += ' ' + name;
        trelloHtml += '</label>';
        trelloHtml += '</div>';
      });
      trelloWrapper.innerHTML = trelloHtml;
    }

    // Sync Jira export_agents checkboxes for each type
    [
      { id: "jira-software-export-agents",    field: "integrations[jira][software][export_agents]" },
      { id: "jira-service-desk-export-agents", field: "integrations[jira][service_desk][export_agents]" },
      { id: "jira-business-export-agents",    field: "integrations[jira][business][export_agents]" }
    ].forEach(function (cfg) {
      var wrapper = document.getElementById(cfg.id);
      if (!wrapper) return;
      var checkedValues = new Set();
      wrapper.querySelectorAll("input[name='" + cfg.field + "']:checked").forEach(function (checkbox) {
        checkedValues.add(checkbox.value);
      });
      var html = "";
      names.forEach(function (name) {
        var checked = checkedValues.has(name) ? " checked" : "";
        html += '<div class="form-group form-group--inline">';
        html += '<label>';
        html += '<input type="checkbox" name="' + cfg.field + '" value="' + name + '"' + checked + '>';
        html += ' ' + name;
        html += '</label>';
        html += '</div>';
      });
      wrapper.innerHTML = html;
    });
  }

  function syncFormState() {
    if (!isConfigPage()) {
      updateSubmitState();
      return;
    }

    syncSingleAssistantMode();
    syncHumanGateFields();
    syncMaxIterationsLimit();
    syncTeamTypeFields();
    syncIntegrationsFields();
    syncExportAgentCheckboxes();
    syncMcpDedicatedVisibility();
    updateSubmitState();
    syncProviderConfigState("trello");
  }

  function reindexAgents() {
    var container = document.getElementById("agents-container");
    if (!container) return;

    var cards = container.querySelectorAll(".agent-card");
    cards.forEach(function (card, idx) {
      card.setAttribute("data-agent-index", idx);

      var numEl = card.querySelector(".agent-card__number");
      if (numEl) numEl.textContent = "Agent #" + (idx + 1);

      card.querySelectorAll("[name]").forEach(function (el) {
        el.name = el.name.replace(/agents\[\d+\]/, "agents[" + idx + "]");
      });

      card.querySelectorAll("[data-agent-index]").forEach(function (el) {
        el.setAttribute("data-agent-index", idx);
      });
    });
  }

  document.body.addEventListener("click", function (e) {
    if (!e.target.matches("#add-agent-btn")) return;

    var container = document.getElementById("agents-container");
    if (!container) return;

    var template = document.getElementById("agent-card-template");
    if (!template) return;

    var cards = container.querySelectorAll(".agent-card");
    var nextIdx = cards.length;

    var clone = template.content.cloneNode(true);
    var html = clone.firstElementChild.outerHTML.replace(/__IDX__/g, nextIdx);

    container.insertAdjacentHTML("beforeend", html);
    reindexAgents();
    syncFormState();
  });

  document.body.addEventListener("click", function (e) {
    if (!e.target.matches(".remove-agent-btn")) return;

    var card = e.target.closest(".agent-card");
    if (!card) return;

    var container = document.getElementById("agents-container");
    if (container && container.querySelectorAll(".agent-card").length <= 1) {
      alert("At least one agent is required.");
      return;
    }

    card.remove();
    reindexAgents();
    syncFormState();
  });

  document.body.addEventListener("click", function (e) {
    var btn = e.target.closest(".sidebar__delete");
    if (!btn) return;

    var blocked = btn.dataset.deleteBlocked === "true";
    if (!blocked) return;

    e.preventDefault();
    e.stopPropagation();
    alert(btn.dataset.blockedTitle || "Cannot delete project while chat sessions exist.");
  }, true);

  document.body.addEventListener("htmx:beforeRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (!elt) return;

    var btn = elt.closest ? elt.closest(".sidebar__delete") : null;
    if (!btn) return;

    if (btn.dataset.deleteBlocked === "true") {
      e.preventDefault();
      alert(btn.dataset.blockedTitle || "Cannot delete project while chat sessions exist.");
    }
  });

  document.body.addEventListener("input", function (e) {
    if (e.target.id === "global-secret-key") {
      updateSubmitState();
    }
    if (e.target.id === "trello-app-name") {
      syncTrelloConfigState();
    }
    if (e.target.name && /agents\[\d+\]\[name\]/.test(e.target.name)) {
      syncExportAgentCheckboxes();
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
      syncProviderConfigState("trello");
    }
    if (e.target.classList && e.target.classList.contains("js-mcp-tools-select")) {
      syncMcpDedicatedVisibility();
    }
  });

  document.body.addEventListener("htmx:beforeRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (!elt || !elt.matches || !elt.matches("form.config-form")) return;
    var errors = validateMcpJsonOnSubmit(elt);
    if (errors.length) {
      e.preventDefault();
      alert("MCP configuration is invalid:\n\n• " + errors.join("\n• "));
    }
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    syncFormState();
  });

  // ---- MCP Secrets KV rows ----
  function reindexMcpSecrets() {
    var container = document.getElementById("mcp-secrets-rows");
    if (!container) return;
    container.querySelectorAll(".mcp-secrets__row").forEach(function (row, idx) {
      row.setAttribute("data-secret-index", idx);
      var k = row.querySelector(".js-mcp-secret-key");
      var v = row.querySelector(".js-mcp-secret-value");
      if (k) k.name = "mcp_secrets[" + idx + "][key]";
      if (v) v.name = "mcp_secrets[" + idx + "][value]";
    });
  }

  document.body.addEventListener("click", function (e) {
    if (e.target.matches(".js-add-mcp-secret")) {
      var container = document.getElementById("mcp-secrets-rows");
      if (!container) return;
      var idx = container.querySelectorAll(".mcp-secrets__row").length;
      var html =
        '<div class="mcp-secrets__row" data-secret-index="' + idx + '">' +
          '<input type="text" class="input input--sm js-mcp-secret-key" ' +
            'name="mcp_secrets[' + idx + '][key]" placeholder="GITHUB_PAT" ' +
            'pattern="^[A-Z][A-Z0-9_]*$" autocomplete="off">' +
          '<input type="password" class="input input--sm js-mcp-secret-value" ' +
            'name="mcp_secrets[' + idx + '][value]" placeholder="secret value" ' +
            'autocomplete="new-password">' +
          '<button type="button" class="chat-session-item__delete js-delete-mcp-secret" ' +
            'aria-label="Remove secret" title="Remove secret">×</button>' +
        '</div>';
      container.insertAdjacentHTML("beforeend", html);
      return;
    }
    if (e.target.matches(".js-delete-mcp-secret")) {
      var row = e.target.closest(".mcp-secrets__row");
      if (row) {
        row.remove();
        reindexMcpSecrets();
      }
    }
  });

  // ---- MCP OAuth Configs rows ----
  function reindexMcpOauthConfigs() {
    var container = document.getElementById("mcp-oauth-configs-rows");
    if (!container) return;
    container.querySelectorAll(".mcp-oauth-configs__row").forEach(function (row, idx) {
      row.setAttribute("data-oauth-index", idx);
      row.querySelectorAll("[name^='mcp_oauth_configs[']").forEach(function (el) {
        el.name = el.name.replace(/^mcp_oauth_configs\[\d+\]/, "mcp_oauth_configs[" + idx + "]");
      });
    });
  }

  document.body.addEventListener("click", function (e) {
    // Add new OAuth config row
    if (e.target.matches(".js-add-mcp-oauth-config")) {
      var container = document.getElementById("mcp-oauth-configs-rows");
      if (!container) return;
      var idx = container.querySelectorAll(".mcp-oauth-configs__row").length;
      var html =
        '<fieldset class="mcp-oauth-configs__row form-group--nested" data-oauth-index="' + idx + '">' +
          '<div class="mcp-oauth-configs__row-header">' +
            '<strong>New OAuth Config</strong>' +
            '<div class="mcp-oauth-configs__row-actions">' +
              '<span class="mcp-oauth-status"></span>' +
              '<button type="button" class="chat-session-item__delete js-delete-mcp-oauth-config" ' +
                'aria-label="Remove OAuth config" title="Remove OAuth config">×</button>' +
            '</div>' +
          '</div>' +
          '<div class="form-group">' +
            '<label>Server Name</label>' +
            '<input type="text" class="input input--sm js-mcp-oauth-server-name" ' +
              'name="mcp_oauth_configs[' + idx + '][server_name]" placeholder="my-api-server" autocomplete="off">' +
            '<small class="form-hint">Must match a key in mcpServers (shared or dedicated config).</small>' +
          '</div>' +
          '<div class="form-group">' +
            '<label>Authorization URL</label>' +
            '<input type="url" class="input input--sm" ' +
              'name="mcp_oauth_configs[' + idx + '][auth_url]" placeholder="https://provider.example.com/oauth/authorize" autocomplete="off">' +
            '<small class="form-hint">The provider\'s authorization endpoint (where users grant consent).</small>' +
          '</div>' +
          '<div class="form-group">' +
            '<label>Token URL</label>' +
            '<input type="url" class="input input--sm" ' +
              'name="mcp_oauth_configs[' + idx + '][token_url]" placeholder="https://provider.example.com/oauth/token" autocomplete="off">' +
            '<small class="form-hint">The provider\'s token endpoint (server-to-server code exchange).</small>' +
          '</div>' +
          '<div class="form-group">' +
            '<label>Client ID</label>' +
            '<input type="text" class="input input--sm" ' +
              'name="mcp_oauth_configs[' + idx + '][client_id]" placeholder="your-client-id" autocomplete="off">' +
          '</div>' +
          '<div class="form-group">' +
            '<label>Client Secret</label>' +
            '<input type="password" class="input input--sm" ' +
              'name="mcp_oauth_configs[' + idx + '][client_secret]" placeholder="your-client-secret" autocomplete="new-password">' +
            '<small class="form-hint">Stored encrypted on the server; masked on page load.</small>' +
          '</div>' +
          '<div class="form-group">' +
            '<label>Scopes <small class="form-hint">(optional)</small></label>' +
            '<input type="text" class="input input--sm" ' +
              'name="mcp_oauth_configs[' + idx + '][scopes]" placeholder="read write offline_access" autocomplete="off">' +
            '<small class="form-hint">Space-separated OAuth scopes to request.</small>' +
          '</div>' +
        '</fieldset>';
      container.insertAdjacentHTML("beforeend", html);
      return;
    }

    // Delete OAuth config row
    if (e.target.matches(".js-delete-mcp-oauth-config")) {
      var row = e.target.closest(".mcp-oauth-configs__row");
      if (row) {
        row.remove();
        reindexMcpOauthConfigs();
      }
      return;
    }

    // Test Authorization button
    if (e.target.matches(".js-test-mcp-oauth")) {
      var btn = e.target;
      var serverNameInput = btn.closest(".mcp-oauth-configs__row")
        && btn.closest(".mcp-oauth-configs__row").querySelector(".js-mcp-oauth-server-name");
      var serverName = (btn.dataset.serverName || (serverNameInput && serverNameInput.value) || "").trim();
      var projectIdInput = document.getElementById("config-project-id");
      var projectId = (btn.dataset.projectId || (projectIdInput && projectIdInput.value) || "").trim();
      if (!serverName) { alert("Enter the Server Name first."); return; }
      if (!projectId) { alert("Save the project before testing OAuth — project_id is missing."); return; }

      var secretKey = (window.AppCommon && window.AppCommon.getSecretKey) ? window.AppCommon.getSecretKey() : "";
      if (!secretKey) { alert("Enter the Secret Key first."); return; }

      var params = new URLSearchParams({
        flow: "test",
        server_name: serverName,
        project_id: projectId,
        skey: secretKey,
      });
      window.open(
        "/mcp/oauth/start/?" + params.toString(),
        "mcp_oauth_test_" + serverName,
        "width=860,height=720,toolbar=0,menubar=0,location=0,status=0"
      );
      return;
    }
  });

  // Listen for OAuth test result postMessages
  window.addEventListener("message", function (event) {
    var data = event.data || {};
    if (data.type !== "mcp_oauth_test_done") return;
    var serverName = data.server_name || "";
    // Find the status badge for this server and update it
    var badge = document.querySelector(
      ".mcp-oauth-status[data-server-name=\"" + serverName.replace(/"/g, '\\"') + "\"]"
    );
    if (!badge) {
      // Newly-added rows don't have data-server-name yet; find by server-name input value
      var rows = document.querySelectorAll(".mcp-oauth-configs__row");
      rows.forEach(function (row) {
        var nameInput = row.querySelector(".js-mcp-oauth-server-name");
        if (nameInput && nameInput.value === serverName) {
          badge = row.querySelector(".mcp-oauth-status");
        }
      });
    }
    if (badge) {
      badge.textContent = data.success ? "✓ Authorized" : "✗ Failed";
      badge.className = "mcp-oauth-status mcp-oauth-status--" + (data.success ? "ok" : "error");
    }
  });

  // ---- Human Gate: Remote Users rows ----
  function _genUuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    // RFC 4122 v4 fallback
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function reindexRemoteUsers() {
    var container = document.getElementById("remote-users-rows");
    if (!container) return;
    container.querySelectorAll(".remote-users__row").forEach(function (row, idx) {
      row.setAttribute("data-remote-index", idx);
      row.querySelectorAll("[name^='human_gate[remote_users]']").forEach(function (el) {
        el.name = el.name.replace(
          /^human_gate\[remote_users\]\[\d+\]/,
          "human_gate[remote_users][" + idx + "]"
        );
      });
      // Reindex matching label/input ids so labels stay associated.
      var nameInput = row.querySelector(".js-remote-user-name");
      if (nameInput) nameInput.id = "remote_user_name_" + idx;
      var nameLabel = row.querySelector("label[for^='remote_user_name_']");
      if (nameLabel) nameLabel.htmlFor = "remote_user_name_" + idx;
      var descInput = row.querySelector(".js-remote-user-description");
      if (descInput) descInput.id = "remote_user_desc_" + idx;
      var descLabel = row.querySelector("label[for^='remote_user_desc_']");
      if (descLabel) descLabel.htmlFor = "remote_user_desc_" + idx;
    });
  }

  document.body.addEventListener("click", function (e) {
    if (e.target.matches(".js-add-remote-user")) {
      var container = document.getElementById("remote-users-rows");
      if (!container) return;
      var idx = container.querySelectorAll(".remote-users__row").length;
      var rid = _genUuid();
      var html =
        '<fieldset class="remote-users__row form-group--nested" data-remote-index="' + idx + '">' +
          '<input type="hidden" class="js-remote-user-id" ' +
            'name="human_gate[remote_users][' + idx + '][id]" value="' + rid + '">' +
          '<div class="remote-users__row-header">' +
            '<strong>New remote user</strong>' +
            '<button type="button" class="chat-session-item__delete js-delete-remote-user" ' +
              'aria-label="Remove remote user" title="Remove remote user">×</button>' +
          '</div>' +
          '<div class="form-row">' +
            '<div class="form-group">' +
              '<label for="remote_user_name_' + idx + '">Name</label>' +
              '<input type="text" id="remote_user_name_' + idx + '" ' +
                'class="input input--sm js-remote-user-name" ' +
                'name="human_gate[remote_users][' + idx + '][name]" ' +
                'placeholder="e.g. Alice" autocomplete="off">' +
            '</div>' +
          '</div>' +
          '<div class="form-group">' +
            '<label for="remote_user_desc_' + idx + '">Description</label>' +
            '<textarea id="remote_user_desc_' + idx + '" ' +
              'class="input input--textarea js-remote-user-description" ' +
              'name="human_gate[remote_users][' + idx + '][description]" rows="2" ' +
              'placeholder="Role / responsibility — shown to agents and used by Selector routing."></textarea>' +
            '<small class="form-hint">Plain-language role description shown to the agent team when this remote user is addressed.</small>' +
          '</div>' +
        '</fieldset>';
      container.insertAdjacentHTML("beforeend", html);
      return;
    }
    if (e.target.matches(".js-delete-remote-user")) {
      var row = e.target.closest(".remote-users__row");
      if (row) {
        row.remove();
        reindexRemoteUsers();
      }
    }
  });

  syncFormState();
});
