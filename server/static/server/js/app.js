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
    }
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

    // Add export buttons to gate panel when export_agent is blank (export on gate)
    var exportHtml = "";
    if (data.export && data.export.enabled && !data.export.export_agent) {
      exportHtml = buildExportButtons(data.export);
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
    if (!exportMeta.export_agent) return true;
    return exportMeta.export_agent.toLowerCase() === (agentName || "").toLowerCase();
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
