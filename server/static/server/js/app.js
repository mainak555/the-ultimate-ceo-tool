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

    document.querySelectorAll(".config-form button[type='submit']").forEach(function (button) {
      button.disabled = !hasSecret;
      button.title = hasSecret ? "" : "Enter the Secret Key in the header before saving.";
    });

    // Show/hide delete buttons in the sidebar
    document.querySelectorAll(".sidebar__delete").forEach(function (btn) {
      btn.hidden = !hasSecret;
    });
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

  function syncFormState() {
    syncHumanGateFields();
    syncMaxIterationsLimit();
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
  });

  document.body.addEventListener("change", function (e) {
    if (e.target.id !== "human-gate-enabled") return;
    syncHumanGateFields();
    syncMaxIterationsLimit();
  });

  // -----------------------------------------------------------------------
  // Auto-dismiss toast alerts after 4 seconds
  // -----------------------------------------------------------------------
  document.body.addEventListener("htmx:afterSwap", function () {
    syncFormState();

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
  // Chat UI — home page interactions
  // =========================================================================

  var chatMessages = document.getElementById("chat-messages");
  var chatInput = document.getElementById("chat-input");
  var chatSendBtn = document.getElementById("chat-send-btn");
  var chatProjectBtn = document.getElementById("chat-project-btn");
  var activeProjectIdInput = document.getElementById("active-project-id");
  var newChatBtn = document.getElementById("new-chat-btn");
  var newSessionModal = document.getElementById("new-session-modal");
  var modalProjectId = document.getElementById("modal-project-id");
  var sessionDescription = document.getElementById("session-description");
  var descCharCount = document.getElementById("desc-char-count");

  // Only wire up when chat elements exist (home page only)
  if (!chatMessages || !chatInput) return;

  // -----------------------------------------------------------------------
  // Auth state: show/hide chat controls based on secret key
  // -----------------------------------------------------------------------
  function updateChatAuthState() {
    var keyInput = getSecretKeyInput();
    var hasSecret = !!(keyInput && keyInput.value.trim());

    if (newChatBtn) newChatBtn.hidden = !hasSecret;

    document.querySelectorAll(".chat-session-item__delete").forEach(function (btn) {
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
    if (!hasSecret && newSessionModal && !newSessionModal.hidden) {
      closeModal();
    }
  }

  // -----------------------------------------------------------------------
  // Modal open / close
  // -----------------------------------------------------------------------
  function openModal() {
    if (!newSessionModal) return;
    if (modalProjectId) modalProjectId.value = activeProjectIdInput ? activeProjectIdInput.value : "";
    if (sessionDescription) { sessionDescription.value = ""; sessionDescription.focus(); }
    if (descCharCount) descCharCount.textContent = "0";
    newSessionModal.hidden = false;
  }

  function closeModal() {
    if (newSessionModal) newSessionModal.hidden = true;
  }

  if (newChatBtn) {
    newChatBtn.addEventListener("click", function () {
      var keyInput = getSecretKeyInput();
      if (!keyInput || !keyInput.value.trim()) {
        return; // button should be hidden; guard against CSS override
      }
      var projectId = activeProjectIdInput ? activeProjectIdInput.value.trim() : "";
      if (!projectId) {
        alert("Select a project first.");
        return;
      }
      openModal();
    });
  }

  var modalCloseBtn = document.getElementById("modal-close-btn");
  var modalCancelBtn = document.getElementById("modal-cancel-btn");
  var modalOverlay = document.getElementById("modal-overlay");

  if (modalCloseBtn) modalCloseBtn.addEventListener("click", closeModal);
  if (modalCancelBtn) modalCancelBtn.addEventListener("click", closeModal);
  if (modalOverlay) modalOverlay.addEventListener("click", closeModal);

  // Close modal when HTMX signals chatSessionCreated
  document.body.addEventListener("chatSessionCreated", closeModal);

  // Allow HTMX to swap 4xx error responses into #new-session-form-feedback
  // (by default HTMX 1.x drops non-2xx responses without swapping)
  document.body.addEventListener("htmx:beforeSwap", function (e) {
    if (e.detail.target && e.detail.target.id === "new-session-form-feedback") {
      if (e.detail.xhr.status === 400 || e.detail.xhr.status === 403) {
        e.detail.shouldSwap = true;
        e.detail.isError = false;
      }
    }
  });

  // -----------------------------------------------------------------------
  // Description char counter
  // -----------------------------------------------------------------------
  if (sessionDescription && descCharCount) {
    sessionDescription.addEventListener("input", function () {
      descCharCount.textContent = sessionDescription.value.length;
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
  // Send button — append human bubble (UI only for now)
  // -----------------------------------------------------------------------
  if (chatSendBtn) {
    chatSendBtn.addEventListener("click", function () {
      if (chatSendBtn.disabled) return;
      var text = chatInput.value.trim();
      if (!text) return;

      // TODO: wire to AutoGen execution endpoint
      chatInput.value = "";
      chatInput.style.height = "auto";
      chatInput.focus();
    });
  }

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

    // Track active project for modal
    if (activeProjectIdInput) activeProjectIdInput.value = projectId || "";
  });

  // Show/hide delete buttons after HTMX swaps new session list items
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

});
