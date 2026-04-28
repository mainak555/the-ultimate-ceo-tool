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

  function renderMarkdown(text) {
    if (window.MarkdownViewer && typeof window.MarkdownViewer.render === "function") {
      return window.MarkdownViewer.render(text || "");
    }
    return (typeof marked !== "undefined")
      ? marked.parse(text || "")
      : "<p>" + String(text || "").replace(/</g, "&lt;") + "</p>";
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
      agentModalBody.innerHTML = renderMarkdown(systemPrompt);
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
  var chatAttachBtn = document.getElementById("chat-attach-btn");
  var chatAttachmentInput = document.getElementById("chat-attachment-input");
  var chatComposeAttachments = document.getElementById("chat-compose-attachments");
  var chatProjectBtn = document.getElementById("chat-project-btn");
  var activeProjectIdInput = document.getElementById("active-project-id");
  var activeSessionIdInput = document.getElementById("active-session-id");
  var csrfToken = (document.getElementById("csrf-token-value") || {}).value || "";

  var composePendingFiles = [];
  var composeUploaded = [];

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatBytes(size) {
    var n = Number(size || 0);
    if (!n) return "0 B";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  var _FILE_ICON_EXTS = {
    pdf: 1, doc: 1, docx: 1, xls: 1, xlsx: 1, ppt: 1, pptx: 1,
    csv: 1, txt: 1, json: 1, xml: 1, md: 1,
  };

  function _iconUrlForFile(filename) {
    var ext = (filename || "").split(".").pop().toLowerCase();
    var name = _FILE_ICON_EXTS[ext] ? ext : "document";
    return "/static/server/assets/icons/file-" + name + ".svg";
  }

  function attachmentChipHtml(att, target, index) {
    var name = escapeHtml(att.filename || "file");
    var url = att.content_url || "";
    var iconCls = att.is_image
      ? "chat-attachment-chip__thumb"
      : "chat-attachment-chip__thumb chat-attachment-chip__thumb--icon";
    var thumb = att.thumbnail_url
      ? '<img class="' + iconCls + '" src="' + att.thumbnail_url + '" alt="' + name + '">'
      : "";
    var openTag = url
      ? '<a class="chat-attachment-chip__file" href="' + url + '" target="_blank" rel="noopener noreferrer">'
      : '<span class="chat-attachment-chip__file">';
    var closeTag = url ? "</a>" : "</span>";

    return '<div class="chat-attachment-chip">'
      + thumb
      + openTag
      + '<span class="chat-attachment-chip__name">' + name + '</span>'
      + '<span class="chat-attachment-chip__meta">' + formatBytes(att.size_bytes) + '</span>'
      + closeTag
      + '<button class="chat-attachment-chip__remove" type="button" data-attachment-target="' + target + '" data-attachment-index="' + index + '">&#x00D7;</button>'
      + '</div>';
  }

  function renderAttachmentList(container, records, target) {
    if (!container) return;
    if (!records || !records.length) {
      container.hidden = true;
      container.innerHTML = "";
      return;
    }
    var html = "";
    records.forEach(function (att, idx) {
      html += attachmentChipHtml(att, target, idx);
    });
    container.innerHTML = html;
    container.hidden = false;
  }

  function renderComposeAttachments() {
    renderAttachmentList(chatComposeAttachments, composeUploaded, "compose");
  }

  function toUploadRecord(file) {
    var isImg = /^image\//i.test(file.type || "");
    return {
      id: "",
      filename: file.name,
      mime_type: file.type || "application/octet-stream",
      size_bytes: file.size || 0,
      is_image: isImg,
      thumbnail_url: isImg ? "" : _iconUrlForFile(file.name),
      content_url: "",
      _file: file,
    };
  }

  function uploadAttachmentRecords(sessionId, records) {
    if (!records || !records.length) return Promise.resolve([]);
    var secretKey = getSecretKey();
    if (!secretKey) return Promise.reject(new Error("Enter the Secret Key first."));

    var form = new FormData();
    records.forEach(function (rec) {
      if (rec && rec._file) form.append("files", rec._file);
    });
    return fetch("/chat/sessions/" + sessionId + "/attachments/", {
      method: "POST",
      headers: {
        "X-App-Secret-Key": secretKey,
        "X-CSRFToken": csrfToken,
      },
      body: form,
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || "Attachment upload failed");
        return data.attachments || [];
      });
    });
  }

  function renderMessageAttachments(attachments) {
    var list = attachments || [];
    if (!list.length) return "";
    var html = '<div class="chat-message-attachments">';
    list.forEach(function (att) {
      var name = escapeHtml(att.filename || "file");
      var url = att.content_url || "";
      var iconCls = att.is_image
        ? "chat-message-attachment__thumb"
        : "chat-message-attachment__thumb chat-message-attachment__thumb--icon";
      var thumb = att.thumbnail_url
        ? '<img class="' + iconCls + '" src="' + att.thumbnail_url + '" alt="' + name + '">'
        : "";
      html += '<a class="chat-message-attachment" href="' + url + '" target="_blank" rel="noopener noreferrer">'
        + thumb
        + '<span class="chat-message-attachment__name">' + name + '</span>'
        + "</a>";
    });
    html += "</div>";
    return html;
  }

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
    if (chatAttachBtn) {
      chatAttachBtn.disabled = !hasSecret;
      chatAttachBtn.title = hasSecret ? "Attach files" : "Enter the Secret Key to attach files.";
    }
    if (chatAttachmentInput) {
      chatAttachmentInput.disabled = !hasSecret;
    }
    if (chatInput) {
      chatInput.disabled = !hasSecret;
      chatInput.placeholder = hasSecret ? "Send a message" : "Enter the Secret Key to send messages.";
    }

    document.querySelectorAll(".human-gate-btn--attach").forEach(function (btn) {
      btn.disabled = !hasSecret;
      btn.title = hasSecret ? "Attach files" : "Enter the Secret Key to attach files.";
    });
    document.querySelectorAll(".human-gate-panel__file-input").forEach(function (input) {
      input.disabled = !hasSecret;
    });

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

  function clearComposeAttachments() {
    composePendingFiles = [];
    composeUploaded = [];
    renderComposeAttachments();
  }

  function addComposeFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    var cap = 10 - composePendingFiles.length - composeUploaded.length;
    if (cap <= 0) return;
    files.slice(0, cap).forEach(function (file) {
      composePendingFiles.push(toUploadRecord(file));
    });
    renderAttachmentList(chatComposeAttachments, composeUploaded.concat(composePendingFiles), "compose");
  }

  function ensureComposeAttachmentsUploaded(sessionId) {
    if (!composePendingFiles.length) {
      return Promise.resolve(composeUploaded.map(function (x) { return x.id; }));
    }

    var pending = composePendingFiles.slice();
    composePendingFiles = [];
    return uploadAttachmentRecords(sessionId, pending)
      .then(function (uploaded) {
        composeUploaded = composeUploaded.concat(uploaded || []);
        renderComposeAttachments();
        return composeUploaded.map(function (x) { return x.id; });
      })
      .catch(function (err) {
        composePendingFiles = pending.concat(composePendingFiles);
        renderAttachmentList(chatComposeAttachments, composeUploaded.concat(composePendingFiles), "compose");
        throw err;
      });
  }

  var gateAttachmentState = new WeakMap();

  function getGateState(panel) {
    var state = gateAttachmentState.get(panel);
    if (!state) {
      state = { pending: [], uploaded: [] };
      gateAttachmentState.set(panel, state);
    }
    return state;
  }

  // Re-evaluate Continue button enabled state for single-assistant chat mode.
  // Text is ALWAYS required; attachments alone are not sufficient.
  function _evalGateContinue(panel) {
    if (!panel || panel.dataset.chatMode !== "single_assistant") return;
    // Never re-enable once a submit is in-flight (panel.dataset.submitting="1").
    // The panel is removed on success; state is restored on failure.
    if (panel.dataset.submitting === "1") return;
    var ta = panel.querySelector(".human-gate-panel__textarea");
    var hasText = ta && ta.value.trim().length > 0;
    var continueBtn = panel.querySelector(".human-gate-btn--continue");
    if (continueBtn) continueBtn.disabled = !hasText;
  }

  function renderGateAttachments(panel) {
    var state = getGateState(panel);
    var listEl = panel.querySelector(".human-gate-panel__attachment-list");
    renderAttachmentList(listEl, state.uploaded.concat(state.pending), "gate");
    // Keep Continue enabled/disabled in sync for single-assistant mode
    _evalGateContinue(panel);
  }

  function addGateFiles(panel, fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    var state = getGateState(panel);
    var cap = 10 - state.pending.length - state.uploaded.length;
    if (cap <= 0) return;
    files.slice(0, cap).forEach(function (file) {
      state.pending.push(toUploadRecord(file));
    });
    renderGateAttachments(panel);
  }

  function ensureGateAttachmentsUploaded(panel, sessionId) {
    var state = getGateState(panel);
    if (!state.pending.length) {
      return Promise.resolve(state.uploaded.map(function (x) { return x.id; }));
    }
    var pending = state.pending.slice();
    state.pending = [];
    return uploadAttachmentRecords(sessionId, pending)
      .then(function (uploaded) {
        state.uploaded = state.uploaded.concat(uploaded || []);
        renderGateAttachments(panel);
        return state.uploaded.map(function (x) { return x.id; });
      })
      .catch(function (err) {
        state.pending = pending.concat(state.pending);
        renderGateAttachments(panel);
        throw err;
      });
  }

  if (chatAttachBtn && chatAttachmentInput) {
    chatAttachBtn.addEventListener("click", function () {
      chatAttachmentInput.click();
    });
    chatAttachmentInput.addEventListener("change", function () {
      addComposeFiles(chatAttachmentInput.files);
      chatAttachmentInput.value = "";
    });
  }

  chatInput.addEventListener("paste", function (e) {
    var files = (e.clipboardData && e.clipboardData.files) || [];
    if (files.length) {
      e.preventDefault();
      addComposeFiles(files);
    }
  });

  chatInput.addEventListener("dragover", function (e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });

  chatInput.addEventListener("drop", function (e) {
    e.preventDefault();
    addComposeFiles((e.dataTransfer && e.dataTransfer.files) || []);
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

  function appendHumanBubble(text, attachments) {
    var ts = new Date().toISOString();
    var contentHtml = renderMarkdown(text);
    var attachmentsHtml = renderMessageAttachments(attachments || []);
    appendBubble(
      '<div class="chat-bubble chat-bubble--human">'
      + '<div class="chat-bubble__meta">'
      + '<span class="chat-bubble__name">You</span>'
      + '<span class="chat-bubble__time"><time class="local-time" data-utc="' + ts + '">' + ts + '</time></span>'
      + '</div>'
      + '<div class="chat-bubble__content">' + contentHtml + '</div>'
      + attachmentsHtml
      + '</div>'
    );
    window.renderLocalTimes();
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
    var isSingleAssistant = data && data.chat_mode === "single_assistant";
    var promptText;
    if (isSingleAssistant) {
      promptText = ' - Round ' + data.round + ' complete. Continue or Stop?';
    } else {
      promptText = ' - Round ' + data.round + ' of ' + data.max_rounds + ' complete. What would you like to do?';
    }
    // Single-assistant mode: Continue starts disabled until user types or attaches
    var continueBtnAttr = isSingleAssistant ? ' disabled' : '';
    var textareaPlaceholder = isSingleAssistant
      ? 'Enter your next message (required)...'
      : 'Optional details to send to agents...';
    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="human-gate-panel" data-session-id="' + sessionId + '"'
      + (isSingleAssistant ? ' data-chat-mode="single_assistant"' : '')
      + '>'
      + '<div class="human-gate-panel__prompt">'
      + '<strong>' + (data.human_name || "You") + '</strong>'
      + promptText
      + '</div>'
      + '<div class="human-gate-panel__decision-row">'
      + '<button class="btn btn--success human-gate-btn human-gate-btn--approve">Approve</button>'
      + '<button class="btn btn--warning human-gate-btn human-gate-btn--reject">Reject</button>'
      + '</div>'
      + '<div class="human-gate-panel__input-row">'
      + '<button class="btn btn--secondary human-gate-btn human-gate-btn--attach chat-attach-btn" type="button" title="Attach files">+</button>'
      + '<textarea class="human-gate-panel__textarea" rows="3" placeholder="' + textareaPlaceholder + '"></textarea>'
      + '<input class="human-gate-panel__file-input" type="file" hidden multiple>'
      + '</div>'
      + '<div class="chat-attachment-list human-gate-panel__attachment-list" hidden></div>'
      + '<div class="human-gate-panel__actions">'
      + '<button class="btn btn--primary human-gate-btn human-gate-btn--continue"' + continueBtnAttr + '>Continue</button>'
      + '<button class="btn btn--danger human-gate-btn human-gate-btn--stop">Stop</button>'
      + '</div>'
      + '</div>'
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function setGateDecision(panel, decision) {
    if (!panel || !decision) return;

    panel.dataset.decision = decision;
    panel.querySelectorAll(".human-gate-btn--approve, .human-gate-btn--reject").forEach(function (btn) {
      btn.classList.remove("is-active");
    });

    var activeBtn = panel.querySelector(
      decision === "APPROVED" ? ".human-gate-btn--approve" : ".human-gate-btn--reject"
    );
    if (activeBtn) activeBtn.classList.add("is-active");

    var ta = panel.querySelector(".human-gate-panel__textarea");
    if (!ta) return;

    var value = ta.value || "";
    var body = value.replace(/^(APPROVED|REJECTED)\s*\n\n?/i, "");
    ta.value = decision + "\n\n" + body;
    ta.focus();
    ta.selectionStart = ta.selectionEnd = ta.value.length;
  }

  function startRun(task, attachmentIds) {
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
    (attachmentIds || []).forEach(function (id) {
      if (id) body.append("attachment_ids", id);
    });

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
      var projectId = activeProjectIdInput ? activeProjectIdInput.value.trim() : "";
      return window.ProviderRegistry.openExportModal(provider, {
        provider:     provider,
        sessionId:    sessionId,
        discussionId: discussionId,
        secretKey:    secretKey,
        csrfToken:    csrfToken,
        projectId:    projectId,
      });
    }
    return false;
  }

  function handleSSEEvent(eventName, data) {
    if (eventName === "message") {
      var ts = data.timestamp || "";
      var initial = (data.agent_name || "A").slice(0, 1).toUpperCase();
      var contentHtml = renderMarkdown(data.content || "");
      var attachmentsHtml = renderMessageAttachments(data.attachments || []);
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
        + '<span class="chat-bubble__time"><time class="local-time" data-utc="' + ts + '">' + ts + '</time></span>'
        + '</div>'
        + '<div class="chat-bubble__content">' + contentHtml + '</div>'
        + attachmentsHtml
        + exportHtml
        + '</div></div>'
      );
      window.renderLocalTimes();
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
      var hasAttachments = composePendingFiles.length > 0 || composeUploaded.length > 0;
      if (!text && !hasAttachments) return;

      function runWithSession(sessionId) {
        // Disable Send immediately to prevent double-submit during the async upload.
        // chatInput stays enabled so the user can keep typing.
        chatSendBtn.disabled = true;
        return ensureComposeAttachmentsUploaded(sessionId).then(function (attachmentIds) {
          var attachmentsForBubble = composeUploaded.slice();
          appendHumanBubble(text || "Attached files", attachmentsForBubble);
          chatInput.value = "";
          chatInput.style.height = "auto";
          chatInput.focus();
          clearComposeAttachments();
          startRun(text, attachmentIds);
        }).catch(function (err) {
          // Restore Send so the user can retry.
          chatSendBtn.disabled = false;
          appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
        });
      }

      var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      if (!sessionId) {
        var projectId = activeProjectIdInput ? activeProjectIdInput.value.trim() : "";
        if (!projectId) { alert("Select a project first."); return; }

        var secretKey = getSecretKey();
        if (!secretKey) { alert("Enter the Secret Key first."); return; }

        var description = (text || "Attachment message").substring(0, 150);

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
          var sid = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
          return runWithSession(sid);
        }).catch(function (err) {
          appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
        }).finally(function () {
          chatSendBtn.disabled = false;
          chatInput.disabled = false;
        });
        return;
      }

      runWithSession(sessionId).catch(function (err) {
        appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
      });
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
    var removeBtn = e.target.closest(".chat-attachment-chip__remove");
    if (!removeBtn) return;
    var target = removeBtn.getAttribute("data-attachment-target") || "";
    var idx = parseInt(removeBtn.getAttribute("data-attachment-index") || "-1", 10);
    if (idx < 0) return;

    if (target === "compose") {
      var uploadedLen = composeUploaded.length;
      if (idx < uploadedLen) {
        composeUploaded.splice(idx, 1);
      } else {
        composePendingFiles.splice(idx - uploadedLen, 1);
      }
      renderAttachmentList(chatComposeAttachments, composeUploaded.concat(composePendingFiles), "compose");
      return;
    }

    if (target === "gate") {
      var panel = removeBtn.closest(".human-gate-panel");
      if (!panel) return;
      var state = getGateState(panel);
      var gateUploadedLen = state.uploaded.length;
      if (idx < gateUploadedLen) {
        state.uploaded.splice(idx, 1);
      } else {
        state.pending.splice(idx - gateUploadedLen, 1);
      }
      renderGateAttachments(panel);
    }
  });

  document.body.addEventListener("click", function (e) {
    var attachBtn = e.target.closest(".human-gate-btn--attach");
    if (!attachBtn) return;
    var panel = attachBtn.closest(".human-gate-panel");
    if (!panel) return;
    var input = panel.querySelector(".human-gate-panel__file-input");
    if (input) input.click();
  });

  document.body.addEventListener("change", function (e) {
    if (!e.target.classList.contains("human-gate-panel__file-input")) return;
    var panel = e.target.closest(".human-gate-panel");
    if (!panel) return;
    addGateFiles(panel, e.target.files);
    e.target.value = "";
  });

  document.body.addEventListener("input", function (e) {
    if (!e.target.classList || !e.target.classList.contains("human-gate-panel__textarea")) return;
    var panel = e.target.closest(".human-gate-panel");
    if (panel) _evalGateContinue(panel);
  });

  document.body.addEventListener("paste", function (e) {
    if (!e.target.classList || !e.target.classList.contains("human-gate-panel__textarea")) return;
    var panel = e.target.closest(".human-gate-panel");
    if (!panel) return;
    var files = (e.clipboardData && e.clipboardData.files) || [];
    if (!files.length) return;
    e.preventDefault();
    addGateFiles(panel, files);
  });

  document.body.addEventListener("dragover", function (e) {
    if (!e.target.classList || !e.target.classList.contains("human-gate-panel__textarea")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });

  document.body.addEventListener("drop", function (e) {
    if (!e.target.classList || !e.target.classList.contains("human-gate-panel__textarea")) return;
    var panel = e.target.closest(".human-gate-panel");
    if (!panel) return;
    e.preventDefault();
    addGateFiles(panel, (e.dataTransfer && e.dataTransfer.files) || []);
  });

  document.body.addEventListener("click", function (e) {
    var panel = e.target.closest(".human-gate-panel");
    if (!panel) return;

    var sessionId = panel.dataset.sessionId
      || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var secretKey = getSecretKey();
    if (!sessionId || !secretKey) return;

    function sendRespond(action, text, attachmentIds) {
      var body = new URLSearchParams({ action: action });
      if (text) body.append("text", text);
      (attachmentIds || []).forEach(function (id) {
        if (id) body.append("attachment_ids", id);
      });
      return fetch("/chat/sessions/" + sessionId + "/respond/", {
        method: "POST",
        headers: {
          "X-App-Secret-Key": secretKey,
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: body.toString(),
      }).then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.error || "Action failed");
          return data;
        });
      });
    }

    if (e.target.closest(".human-gate-btn--approve")) {
      setGateDecision(panel, "APPROVED");
    } else if (e.target.closest(".human-gate-btn--reject")) {
      setGateDecision(panel, "REJECTED");
    } else if (e.target.closest(".human-gate-btn--continue")) {
      var continueBtn = panel.querySelector(".human-gate-btn--continue");
      var ta = panel.querySelector(".human-gate-panel__textarea");
      var text = ta ? ta.value.trim() : "";
      // Text is mandatory for ALL gate modes — attachments alone are not enough.
      // Approve/Reject buttons prepend their decision to the textarea, satisfying this.
      if (!text) {
        if (ta) { ta.focus(); ta.classList.add("input--shake"); setTimeout(function () { ta.classList.remove("input--shake"); }, 400); }
        return;
      }
      // Prevent double-submit while an async file upload is in-flight.
      // Without this guard, clicking Continue twice (or clicking during upload)
      // fires multiple appendHumanBubble + sendRespond calls.
      if (panel.dataset.submitting === "1") return;
      panel.dataset.submitting = "1";
      if (continueBtn) continueBtn.disabled = true;
      ensureGateAttachmentsUploaded(panel, sessionId).then(function (attachmentIds) {
        var state = getGateState(panel);
        var bubbleAttachments = state.uploaded.slice();
        panel.remove();
        if (text || bubbleAttachments.length) {
          appendHumanBubble(text || "Attached files", bubbleAttachments);
        }
        sendRespond("continue", text, attachmentIds).then(function (d) {
          if (d.status === "ok") startRun(d.task || "", d.attachment_ids || []);
        }).catch(function (err) {
          appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
        });
      }).catch(function (err) {
        // Upload failed — restore interactive state so the user can retry.
        panel.dataset.submitting = "";
        if (continueBtn) continueBtn.disabled = false;
        appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
      });
    } else if (e.target.closest(".human-gate-btn--stop")) {
      panel.remove();
      sendRespond("stop", "").then(function () {
        appendStatusBadge("stopped");
      }).catch(function (err) {
        appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
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
