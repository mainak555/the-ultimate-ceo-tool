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

  function syncExportAgentCheckboxes() {
    var wrapper = document.getElementById("integrations-export-agents");
    if (!wrapper) return;

    var checkedValues = new Set();
    wrapper.querySelectorAll("input[name='integrations[trello][export_agents]']:checked").forEach(function (checkbox) {
      checkedValues.add(checkbox.value);
    });

    var names = listAgentNames();
    var html = "";
    names.forEach(function (name) {
      var checked = checkedValues.has(name) ? " checked" : "";
      html += '<div class="form-group form-group--inline">';
      html += '<label>';
      html += '<input type="checkbox" name="integrations[trello][export_agents]" value="' + name + '"' + checked + '>';
      html += ' ' + name;
      html += '</label>';
      html += '</div>';
    });

    wrapper.innerHTML = html;
  }

  function syncFormState() {
    if (!isConfigPage()) {
      updateSubmitState();
      return;
    }

    syncHumanGateFields();
    syncMaxIterationsLimit();
    syncTeamTypeFields();
    syncIntegrationsFields();
    syncExportAgentCheckboxes();
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
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    syncFormState();
  });

  syncFormState();
});
