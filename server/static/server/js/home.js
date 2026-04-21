/**
 * home.js - Home chat feature behavior.
 *
 * Scope:
 *   - Chat session interactions and modal handling
 *   - SSE run stream rendering
 *   - Human gate actions
 *   - Export button delegation to feature-specific exporters
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
    var input = getSecretKeyInput();
    return input ? input.value.trim() : "";
  }

  var agentPromptModal = document.getElementById("agent-prompt-modal");
  var agentModalTitle = document.getElementById("agent-modal-title");
  var agentModalBody = document.getElementById("agent-modal-body");
  var agentModalClose = document.getElementById("agent-modal-close-btn");
  var agentModalOverlay = document.getElementById("agent-modal-overlay");

  function openAgentModal(name, systemPrompt) {
    if (!agentPromptModal) return;
    if (agentModalTitle) agentModalTitle.textContent = name + " - System Prompt";
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

  if (agentModalClose) agentModalClose.addEventListener("click", closeAgentModal);
  if (agentModalOverlay) agentModalOverlay.addEventListener("click", closeAgentModal);

  var chatMessages = document.getElementById("chat-messages");
  var chatInput = document.getElementById("chat-input");
  var chatSendBtn = document.getElementById("chat-send-btn");
  var chatStopBtn = document.getElementById("chat-stop-btn");
  var chatProjectBtn = document.getElementById("chat-project-btn");
  var activeProjectIdInput = document.getElementById("active-project-id");
  var activeSessionIdInput = document.getElementById("active-session-id");
  var csrfToken = (document.getElementById("csrf-token-value") || {}).value || "";

  var editSessionModal = document.getElementById("edit-session-modal");
  var editModalSessionId = document.getElementById("edit-modal-session-id");
  var editSessionDescription = document.getElementById("edit-session-description");
  var editDescCharCount = document.getElementById("edit-desc-char-count");

  if (!chatMessages || !chatInput) return;

  function updateChatAuthState() {
    var hasSecret = !!getSecretKey();

    document.querySelectorAll(".chat-session-item__delete").forEach(function (btn) {
      btn.hidden = !hasSecret;
    });

    document.querySelectorAll(".chat-session-item__edit").forEach(function (btn) {
      btn.hidden = !hasSecret;
    });

    document.querySelectorAll(".chat-bubble__actions").forEach(function (actions) {
      actions.hidden = !hasSecret;
    });

    if (chatSendBtn) {
      chatSendBtn.disabled = !hasSecret;
      chatSendBtn.title = hasSecret ? "Send" : "Enter the Secret Key to send messages.";
    }
    if (chatInput) {
      chatInput.disabled = !hasSecret;
      chatInput.placeholder = hasSecret ? "Send a message" : "Enter the Secret Key to send messages.";
    }

    if (!hasSecret && editSessionModal && !editSessionModal.hidden) {
      closeEditModal();
    }

    if (!hasSecret) {
      closeExportDropdowns();
    }
  }

  function openEditModal(sessionId, description) {
    if (!editSessionModal) return;
    if (editModalSessionId) editModalSessionId.value = sessionId;
    if (editSessionDescription) {
      editSessionDescription.value = description || "";
      editSessionDescription.focus();
    }
    if (editDescCharCount) editDescCharCount.textContent = (description || "").length;
    var form = document.getElementById("edit-session-form");
    if (form) form.setAttribute("hx-post", "/chat/sessions/" + sessionId + "/update/");
    if (typeof htmx !== "undefined" && form) htmx.process(form);
    editSessionModal.hidden = false;
  }

  function closeEditModal() {
    if (editSessionModal) editSessionModal.hidden = true;
  }

  var editModalCloseBtn = document.getElementById("edit-modal-close-btn");
  var editModalCancelBtn = document.getElementById("edit-modal-cancel-btn");
  var editModalOverlay = document.getElementById("edit-modal-overlay");

  if (editModalCloseBtn) editModalCloseBtn.addEventListener("click", closeEditModal);
  if (editModalCancelBtn) editModalCancelBtn.addEventListener("click", closeEditModal);
  if (editModalOverlay) editModalOverlay.addEventListener("click", closeEditModal);

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

  document.body.addEventListener("click", function (e) {
    var editBtn = e.target.closest(".chat-session-item__edit");
    if (!editBtn) return;
    var sessionId = editBtn.dataset.sessionId || "";
    var description = editBtn.dataset.description || "";
    if (!sessionId) return;
    openEditModal(sessionId, description);
  });

  document.body.addEventListener("click", function (e) {
    var item = e.target.closest(".chat-session-item");
    if (!item) return;

    if (e.target.closest(".chat-session-item__edit") || e.target.closest(".chat-session-item__delete")) {
      return;
    }

    var nameEl = item.querySelector(".chat-session-item__name");
    if (!nameEl) return;

    if (e.target.closest(".chat-session-item__name")) {
      return;
    }

    if (typeof htmx !== "undefined") {
      htmx.trigger(nameEl, "click");
    } else {
      nameEl.click();
    }
  });

  document.body.addEventListener("htmx:beforeSwap", function (e) {
    if (e.detail.target && e.detail.target.id === "edit-session-form-feedback") {
      if (e.detail.xhr.status === 400 || e.detail.xhr.status === 403) {
        e.detail.shouldSwap = true;
        e.detail.isError = false;
      }
    }
  });

  document.body.addEventListener("chatSessionUpdated", function () {
    closeEditModal();
  });

  if (editSessionDescription && editDescCharCount) {
    editSessionDescription.addEventListener("input", function () {
      editDescCharCount.textContent = editSessionDescription.value.length;
    });
  }

  chatInput.addEventListener("input", function () {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
  });

  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (chatSendBtn && !chatSendBtn.disabled) chatSendBtn.click();
    }
  });

  var _activeReader = null;

  function setRunningState(running) {
    if (chatInput) { chatInput.disabled = running; }
    if (chatSendBtn) { chatSendBtn.hidden = running; }
    if (chatStopBtn) { chatStopBtn.hidden = !running; }
    document.querySelectorAll(".chat-restart-btn").forEach(function (btn) {
      btn.disabled = running;
    });
    document.querySelectorAll(".chat-restart-panel__textarea").forEach(function (ta) {
      ta.disabled = running;
    });
  }

  function appendBubble(html) {
    var msgs = document.getElementById("chat-history-msgs");
    if (!msgs) {
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
    var label = type === "completed" ? "Run completed" : "Run stopped";
    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-status-badge chat-status-badge--' + type + '">' + label + '</div>'
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function appendRestartPanel() {
    var sessionId = activeSessionIdInput ? activeSessionIdInput.value : "";
    if (!sessionId || document.querySelector(".chat-restart-panel")) return;
    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-restart-panel" data-session-id="' + sessionId + '">'
      + '<div class="chat-restart-panel__title">Restart from saved agent state</div>'
      + '<p class="chat-restart-panel__hint">Continue this conversation from the last checkpoint. You can continue directly or add extra context first.</p>'
      + '<div class="chat-restart-panel__actions">'
      + '<button class="btn btn--success chat-restart-btn chat-restart-btn--continue" data-mode="continue_only">Continue from last</button>'
      + '<button class="btn btn--secondary chat-restart-btn chat-restart-btn--with-context" data-mode="continue_with_context">Add context and continue</button>'
      + '</div>'
      + '<div class="chat-restart-panel__context" hidden>'
      + '<textarea class="input input--textarea chat-restart-panel__textarea" rows="3" placeholder="Add context or instruction before continuing..."></textarea>'
      + '<div class="chat-restart-panel__context-actions">'
      + '<button class="btn btn--primary chat-restart-btn chat-restart-btn--submit" data-mode="continue_with_context">Continue with context</button>'
      + '</div>'
      + '</div>'
      + '</div>'
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function restartSession(panel, mode, text) {
    var sessionId = panel.dataset.sessionId
      || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var secretKey = getSecretKey();
    if (!sessionId || !secretKey) return;

    var body = new URLSearchParams();
    body.append("mode", mode || "continue_only");
    if (text) body.append("text", text);

    setRunningState(true);
    fetch("/chat/sessions/" + sessionId + "/restart/", {
      method: "POST",
      headers: {
        "X-App-Secret-Key": secretKey,
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || "Restart failed");
        return data;
      });
    }).then(function (data) {
      panel.remove();
      if ((mode || "") === "continue_with_context" && text) {
        appendHumanBubble(text);
      }
      startRun(data.task || "");
    }).catch(function (err) {
      setRunningState(false);
      appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
    });
  }

  function appendGatePanel(data) {
    var sessionId = activeSessionIdInput ? activeSessionIdInput.value : "";
    var modeHtml = data.mode === "feedback"
      ? '<textarea class="input input--textarea human-gate-panel__textarea" rows="3" placeholder="Type your feedback for the agents..."></textarea>'
        + '<div class="human-gate-panel__actions">'
        + '<button class="btn btn--primary human-gate-btn human-gate-btn--feedback">Send Feedback</button>'
        + '<button class="btn btn--danger human-gate-btn human-gate-btn--stop">Stop</button>'
        + '</div>'
      : '<div class="human-gate-panel__actions">'
        + '<button class="btn btn--success human-gate-btn human-gate-btn--approve">Approve and Continue</button>'
        + '<button class="btn btn--danger human-gate-btn human-gate-btn--stop">Stop</button>'
        + '</div>';

    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="human-gate-panel" data-session-id="' + sessionId + '">'
      + '<div class="human-gate-panel__prompt">'
      + '<strong>' + (data.human_name || "You") + '</strong>'
      + ' - Round ' + data.round + ' of ' + data.max_rounds + ' complete. What would you like to do?'
      + '</div>'
      + modeHtml
      + '</div>'
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function startRun(task) {
    var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
    if (!sessionId) { return; }

    var secretKey = getSecretKey();
    if (!secretKey) { alert("Enter the Secret Key first."); return; }

    chatMessages.querySelectorAll(".chat-status-badge, .chat-restart-panel").forEach(function (el) {
      el.remove();
    });

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
          buffer = frames.pop();
          frames.forEach(function (frame) {
            var eventMatch = frame.match(/^event: (\w+)/m);
            var dataMatch = frame.match(/^data: (.+)/m);
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

  function getVisibleExportProviders(exportMeta, agentName) {
    if (!exportMeta || !exportMeta.enabled) return [];

    var lower = (agentName || "").toLowerCase();
    return (exportMeta.providers || []).filter(function (provider) {
      var allowlist = provider.export_agents || [];
      if (!allowlist.length) return true;
      return allowlist.some(function (name) {
        return (name || "").toLowerCase() === lower;
      });
    });
  }

  function buildExportDropdown(exportMeta, agentName, sessionId, discussionId) {
    var providers = getVisibleExportProviders(exportMeta, agentName);
    if (!providers.length || !sessionId || !discussionId) return "";

    var html = '<div class="chat-bubble__actions">'
      + '<div class="export-dropdown" data-export-dropdown>'
      + '<button type="button" class="btn btn--sm btn--secondary export-dropdown__toggle" aria-expanded="false">Export \u21D7</button>'
      + '<div class="export-dropdown__menu" hidden>';

    providers.forEach(function (provider) {
      html += '<button type="button" class="export-dropdown__item" data-provider="' + provider.name + '" data-session-id="'
        + sessionId + '" data-discussion-id="' + discussionId + '">' + provider.label + '</button>';
    });

    html += '</div></div></div>';
    return html;
  }

  function closeExportDropdowns() {
    document.querySelectorAll("[data-export-dropdown]").forEach(function (dropdown) {
      var menu = dropdown.querySelector(".export-dropdown__menu");
      var toggle = dropdown.querySelector(".export-dropdown__toggle");
      if (menu) menu.hidden = true;
      if (toggle) toggle.setAttribute("aria-expanded", "false");
    });
  }

  function openProviderExportModal(provider, sessionId, discussionId, secretKey) {
    if (window.ProviderRegistry && typeof window.ProviderRegistry.openExportModal === "function") {
      return window.ProviderRegistry.openExportModal(provider, {
        provider: provider,
        sessionId: sessionId,
        discussionId: discussionId,
        secretKey: secretKey,
        csrfToken: csrfToken,
      });
    }
    return false;
  }

  function handleSSEEvent(eventName, data) {
    if (eventName === "message") {
      var ts = data.timestamp || "";
      var initial = (data.agent_name || "A").slice(0, 1).toUpperCase();
      var contentHtml = (typeof marked !== "undefined")
        ? marked.parse(data.content || "")
        : "<p>" + (data.content || "").replace(/</g, "&lt;") + "</p>";
      var exportHtml = buildExportDropdown(
        data.export,
        data.agent_name,
        activeSessionIdInput ? activeSessionIdInput.value.trim() : "",
        data.id || ""
      );
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
      appendRestartPanel();
    } else if (eventName === "stopped") {
      setRunningState(false);
      appendStatusBadge("stopped");
      appendRestartPanel();
    } else if (eventName === "error") {
      setRunningState(false);
      appendBubble('<div class="chat-bubble chat-bubble--error">' + (data.message || "Unknown error") + '</div>');
    }
  }

  if (chatSendBtn) {
    chatSendBtn.addEventListener("click", function () {
      if (chatSendBtn.disabled) return;
      var text = chatInput.value.trim();
      if (!text) return;

      var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      if (!sessionId) {
        var projectId = activeProjectIdInput ? activeProjectIdInput.value.trim() : "";
        if (!projectId) { alert("Select a project first."); return; }

        var secretKey = getSecretKey();
        if (!secretKey) { alert("Enter the Secret Key first."); return; }

        var description = text.substring(0, 150);

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
          var tmp = document.createElement("div");
          tmp.innerHTML = html;
          tmp.querySelectorAll("[hx-swap-oob]").forEach(function (el) {
            var targetId = el.id;
            var target = document.getElementById(targetId);
            if (target) {
              if (el.tagName === "INPUT") {
                target.outerHTML = el.outerHTML;
                if (targetId === "active-session-id") {
                  activeSessionIdInput = document.getElementById("active-session-id");
                }
              } else {
                target.innerHTML = el.innerHTML;
              }
            }
          });
          updateChatAuthState();

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

  if (chatStopBtn) {
    chatStopBtn.addEventListener("click", function () {
      var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      var secretKey = getSecretKey();
      if (!sessionId || !secretKey) return;
      fetch("/chat/sessions/" + sessionId + "/stop/", {
        method: "POST",
        headers: { "X-App-Secret-Key": secretKey, "X-CSRFToken": csrfToken },
      });
    });
  }

  document.body.addEventListener("click", function (e) {
    var panel = e.target.closest(".human-gate-panel");
    if (!panel) return;

    var sessionId = panel.dataset.sessionId
      || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var secretKey = getSecretKey();
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

  document.body.addEventListener("click", function (e) {
    var panel = e.target.closest(".chat-restart-panel");
    if (!panel) return;

    var withContextBtn = e.target.closest(".chat-restart-btn--with-context");
    if (withContextBtn) {
      var ctx = panel.querySelector(".chat-restart-panel__context");
      if (ctx) {
        ctx.hidden = false;
        var ta = ctx.querySelector(".chat-restart-panel__textarea");
        if (ta) ta.focus();
      }
      return;
    }

    var continueBtn = e.target.closest(".chat-restart-btn--continue");
    if (continueBtn) {
      restartSession(panel, "continue_only", "");
      return;
    }

    var submitBtn = e.target.closest(".chat-restart-btn--submit");
    if (submitBtn) {
      var ta = panel.querySelector(".chat-restart-panel__textarea");
      var text = ta ? ta.value.trim() : "";
      if (!text) {
        if (ta) ta.focus();
        return;
      }
      restartSession(panel, "continue_with_context", text);
    }
  });

  document.body.addEventListener("click", function (e) {
    var item = e.target.closest(".chat-project-item");
    if (!item) return;

    var projectName = item.dataset.project;
    var projectId = item.dataset.projectId;
    if (!projectName) return;

    if (chatProjectBtn) {
      chatProjectBtn.textContent = projectName + " \u25BE";
      chatProjectBtn.dataset.activeProject = projectName;
      chatProjectBtn.dataset.activeProjectId = projectId;
    }

    if (activeProjectIdInput) activeProjectIdInput.value = projectId || "";
    if (activeSessionIdInput) activeSessionIdInput.value = "";
  });

  document.body.addEventListener("htmx:beforeRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (!elt) return;
    var li = elt.closest("li[data-session-id]");
    if (li && activeSessionIdInput) activeSessionIdInput.value = li.dataset.sessionId || "";
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    updateChatAuthState();
  });

  document.body.addEventListener("input", function (e) {
    if (e.target.id === "global-secret-key") {
      updateChatAuthState();
    }
  });

  document.body.addEventListener("click", function (e) {
    var toggle = e.target.closest(".export-dropdown__toggle");
    if (toggle) {
      var dropdown = toggle.closest("[data-export-dropdown]");
      if (!dropdown) return;
      var menu = dropdown.querySelector(".export-dropdown__menu");
      var isOpening = !!(menu && menu.hidden);
      closeExportDropdowns();
      if (menu && isOpening) {
        menu.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
      }
      return;
    }

    var item = e.target.closest(".export-dropdown__item");
    if (item) {
      var sessionId = item.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
      var discussionId = (item.dataset.discussionId || "").trim();
      var secretKey = getSecretKey();
      if (!sessionId || !discussionId || !secretKey) {
        alert("Missing export context. Refresh the session and try again.");
        return;
      }

      var provider = item.dataset.provider;
      if (!openProviderExportModal(provider, sessionId, discussionId, secretKey)) {
        alert(provider + " export is not yet implemented.");
      }
      closeExportDropdowns();
      return;
    }

    if (!e.target.closest("[data-export-dropdown]")) {
      closeExportDropdowns();
    }
  });

  document.body.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeExportDropdowns();
    }
  });

  updateChatAuthState();
});
