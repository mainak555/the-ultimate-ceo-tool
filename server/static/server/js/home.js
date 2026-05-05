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

    if (!hasSecret && editSessionModal && !editSessionModal.hidden) {
      closeEditModal();
    }

    if (!hasSecret) {
      closeExportDropdowns();
    }
    _evalSendBtn();
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

  // ── Gate mode helpers ─────────────────────────────────────────────────────
  // When the SSE "gate" event fires the bottom input bar enters gate mode.
  // The old .human-gate-panel widget is replaced by a non-interactive status
  // badge in chat; Send routes to sendRespond("continue") and Stop routes to
  // sendRespond("stop") instead of the run /stop/ endpoint.

  // POST to the session respond endpoint (gate continue or gate stop).
  function sendRespond(sessionId, action, text, attachmentIds) {
    var secretKey = getSecretKey();
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

  // Re-evaluate Send button enabled state honouring gate mode rules.
  // Single-assistant gate mode requires non-empty text; all other states
  // only need a secret key.
  function _evalSendBtn() {
    if (!chatSendBtn) return;
    if (!getSecretKey()) { chatSendBtn.disabled = true; return; }
    if (_gateData && _gateData.chat_mode === "single_assistant") {
      chatSendBtn.disabled = !chatInput || !chatInput.value.trim();
      return;
    }
    if (_gateData && _gateData.quorum === "all") {
      var hasCompose = composePendingFiles.length > 0 || composeUploaded.length > 0;
      var hasText = !!(chatInput && chatInput.value.trim());
      var canFinalizeEmpty = !!_gateData.awaiting_host_final;
      chatSendBtn.disabled = !(hasText || hasCompose || canFinalizeEmpty);
      return;
    }
    chatSendBtn.disabled = false;
  }

  // Activate gate mode on the bottom input bar.
  function setGateMode(data) {
    _gateData = data;
    if (_gateData && typeof _gateData.awaiting_host_final !== "boolean") {
      _gateData.awaiting_host_final = false;
    }
    var isSingle = data && data.chat_mode === "single_assistant";
    if (chatInput) {
      var roundText = isSingle
        ? "Round " + data.round
        : "Round " + data.round + "/" + data.max_rounds;
      chatInput.placeholder = roundText + " \u2014 enter your response\u2026";
    }
    if (chatStopBtn) chatStopBtn.hidden = false;
    _evalSendBtn();
  }

  // Deactivate gate mode and restore the normal input bar state.
  function clearGateMode() {
    _gateData = null;
    if (chatInput) chatInput.placeholder = "Send a message";
    if (chatStopBtn) chatStopBtn.hidden = true;
    _evalSendBtn();
  }

  // Append a non-interactive gate status badge into the chat history.
  // Carries data-gate-context so page-reload can restore gate mode.
  function appendGateBadge(data) {
    var isSingle = data && data.chat_mode === "single_assistant";
    var roundText = isSingle
      ? "Round " + data.round
      : "Round " + data.round + "/" + data.max_rounds;
    var ctx = escapeHtml(JSON.stringify({
      round: data.round,
      max_rounds: data.max_rounds || 0,
      chat_mode: data.chat_mode || "",
      quorum: data.quorum || "na",
    }));
    chatMessages.insertAdjacentHTML(
      "beforeend",
      "<div class=\"chat-status-badge chat-status-badge--gate\" data-gate-context='" + ctx + "'>"
      + "\u23F8 " + escapeHtml(roundText) + " \u2014 response is required"
      + "</div>"
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // Handle Send while the input bar is in gate mode.
  function _handleGateSend() {
    var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
    var secretKey = getSecretKey();
    if (!sessionId || !secretKey) return;

    var text = chatInput ? chatInput.value.trim() : "";
    var hasAttachments = composePendingFiles.length > 0 || composeUploaded.length > 0;
    var canFinalizeEmpty = !!(_gateData && _gateData.quorum === "all" && _gateData.awaiting_host_final);

    // Single-assistant mode: text is mandatory.
    if (_gateData && _gateData.chat_mode === "single_assistant" && !text) {
      if (chatInput) {
        chatInput.focus();
        chatInput.classList.add("input--shake");
        setTimeout(function () { chatInput.classList.remove("input--shake"); }, 400);
      }
      return;
    }
    if (!text && !hasAttachments && !canFinalizeEmpty) return;

    if (chatSendBtn) chatSendBtn.disabled = true;
    // committed = true once upload succeeded and we've already appended the
    // human bubble + cleared gate mode. After that point we do NOT re-enable
    // Send on error (session reload is the recovery path).
    var committed = false;

    ensureComposeAttachmentsUploaded(sessionId).then(function (attachmentIds) {
      committed = true;
      var attachmentsForBubble = composeUploaded.slice();
      if (text || attachmentsForBubble.length) {
        appendHumanBubble(text || "Attached files", attachmentsForBubble);
      }
      if (chatInput) { chatInput.value = ""; chatInput.style.height = "auto"; chatInput.focus(); }
      clearComposeAttachments();
      clearGateMode();
      return sendRespond(sessionId, "continue", text, attachmentIds);
    }).then(function (d) {
      if (d && d.status === "ok") startRun(d.task || "", d.attachment_ids || []);
    }).catch(function (err) {
      if (!committed && chatSendBtn) chatSendBtn.disabled = false;
      appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
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

  // Keep Send enabled/disabled in sync with typing during gate mode.
  chatInput.addEventListener("input", function () {
    _evalSendBtn();
  });

  var _activeReader = null;
  var _gateData = null; // non-null while the input bar is in human-gate mode
  var _hostSessionWs = null;

  function setRunningState(running) {
    if (chatInput) { chatInput.disabled = running; }
    if (chatSendBtn) { chatSendBtn.hidden = running; }
    if (chatStopBtn) {
      chatStopBtn.hidden = !running;
      chatStopBtn.disabled = false; // reset so next run gets a fresh enabled button
    }
    if (chatAttachBtn) { chatAttachBtn.disabled = running; }
    if (chatAttachmentInput) { chatAttachmentInput.disabled = running; }
    document.querySelectorAll(".chat-restart-btn").forEach(function (btn) {
      btn.disabled = running;
    });
    document.querySelectorAll(".chat-restart-panel__textarea").forEach(function (ta) {
      ta.disabled = running;
    });
    setAgentsWorkingBadge(running);
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

  var _COPY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  var _CHECK_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';

  function _buildCopyBtn() {
    return '<button type="button" class="chat-bubble__copy-btn" title="Copy message" aria-label="Copy message">' + _COPY_ICON + '</button>';
  }

  function _getCopyText(bubbleEl) {
    var md = bubbleEl.dataset.rawContent || "";
    var attachmentNames = bubbleEl.querySelectorAll(".chat-message-attachment__name");
    if (attachmentNames.length) {
      md += "\n\n**Attachments:**\n";
      attachmentNames.forEach(function (span) {
        md += "- " + span.textContent.trim() + "\n";
      });
    }
    return md.trim();
  }

  function _showCopiedFeedback(btn) {
    btn.innerHTML = _CHECK_ICON;
    btn.classList.add("chat-bubble__copy-btn--copied");
    btn.title = "Copied!";
    setTimeout(function () {
      btn.innerHTML = _COPY_ICON;
      btn.classList.remove("chat-bubble__copy-btn--copied");
      btn.title = "Copy message";
    }, 2000);
  }

  function _fallbackCopyText(text, btn) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand("copy"); _showCopiedFeedback(btn); } catch (e) { /* silent */ }
    document.body.removeChild(ta);
  }

  document.body.addEventListener("click", function (e) {
    var btn = e.target.closest(".chat-bubble__copy-btn");
    if (!btn) return;
    var bubble = btn.closest(".chat-bubble");
    if (!bubble) return;
    var text = _getCopyText(bubble);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        _showCopiedFeedback(btn);
      }).catch(function () {
        _fallbackCopyText(text, btn);
      });
    } else {
      _fallbackCopyText(text, btn);
    }
  });

  function appendHumanBubble(text, attachments) {
    var ts = new Date().toISOString();
    var contentHtml = renderMarkdown(text);
    var attachmentsHtml = renderMessageAttachments(attachments || []);
    appendBubble(
      '<div class="chat-bubble chat-bubble--human" data-raw-content="' + escapeHtml(text || "") + '">'
      + '<div class="chat-bubble__meta">'
      + '<span class="chat-bubble__name">You</span>'
      + '<span class="chat-bubble__time"><time class="local-time" data-utc="' + ts + '">' + ts + '</time></span>'
      + _buildCopyBtn()
      + '</div>'
      + '<div class="chat-bubble__content">' + contentHtml + '</div>'
      + attachmentsHtml
      + '</div>'
    );
    window.renderLocalTimes();
  }

  function appendRemoteUserBubble(msg) {
    var ts = msg.timestamp || new Date().toISOString();
    var displayName = msg.agent_name || "Remote User";
    var contentHtml = renderMarkdown(msg.content || "");
    var attachmentsHtml = renderMessageAttachments(msg.attachments || []);
    appendBubble(
      '<div class="chat-bubble chat-bubble--human" data-raw-content="' + escapeHtml(msg.content || "") + '">'
      + '<div class="chat-bubble__meta">'
      + '<span class="chat-bubble__name">' + escapeHtml(displayName) + '</span>'
      + '<span class="chat-bubble__time"><time class="local-time" data-utc="' + ts + '">' + ts + '</time></span>'
      + _buildCopyBtn()
      + '</div>'
      + '<div class="chat-bubble__content">' + contentHtml + '</div>'
      + attachmentsHtml
      + '</div>'
    );
    window.renderLocalTimes();
  }

  function appendStatusBadge(type) {
    var label = type === "completed" ? "Run completed" : "Run stopped";
    setAgentsWorkingBadge(false);
    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-status-badge chat-status-badge--' + type + '">' + label + '</div>'
    );
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function setAgentsWorkingBadge(show) {
    if (!chatMessages) return;
    var existing = chatMessages.querySelector(".chat-status-badge--running");
    if (!show) {
      if (existing) existing.remove();
      return;
    }
    if (existing) return;
    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-status-badge chat-status-badge--running">\u2699 Agents at work</div>'
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

  // ------------------------------------------------------------------
  // MCP OAuth in-history authorization panel
  // ------------------------------------------------------------------

  var _oauthWs = null;
  var _oauthMessageListener = null;

  function _teardownOAuthGate() {
    if (_oauthWs) {
      try { _oauthWs.close(); } catch (_) {}
      _oauthWs = null;
    }
    if (_oauthMessageListener) {
      window.removeEventListener("message", _oauthMessageListener);
      _oauthMessageListener = null;
    }
  }

  function _resolveProjectId() {
    var pid = activeProjectIdInput ? activeProjectIdInput.value.trim() : "";
    if (!pid && chatProjectBtn && chatProjectBtn.dataset.activeProjectId) {
      pid = chatProjectBtn.dataset.activeProjectId;
    }
    return pid;
  }

  /** Build server rows HTML and inject into the panel, removing the loading indicator. */
  function _renderOAuthRows(panel, servers) {
    var rowsContainer = panel.querySelector(".chat-oauth-panel__rows");
    if (!rowsContainer) return;
    // Remove loading indicator if present.
    var loading = rowsContainer.querySelector(".chat-oauth-panel__loading");
    if (loading) loading.remove();

    (servers || []).forEach(function (srv) {
      var name = typeof srv === "string" ? srv : (srv.name || "");
      var authorized = typeof srv === "object" && srv.authorized;
      // Skip if row already exists (idempotent).
      if (panel.querySelector('.chat-oauth-panel__row[data-server-name="' + name.replace(/"/g, '\\"') + '"]')) return;
      var safe = escapeHtml(name);
      var rowHtml = '<div class="chat-oauth-panel__row'
        + (authorized ? " chat-oauth-panel__row--authorized" : "") + '" data-server-name="' + safe + '">'
        + '<span class="chat-oauth-panel__server">' + safe + '</span>'
        + '<span class="chat-oauth-panel__status '
        + (authorized ? "chat-oauth-panel__status--authorized" : "chat-oauth-panel__status--pending") + '">'
        + (authorized ? "Authorized \u2713" : "Pending") + '</span>'
        + '<button type="button" class="btn btn--primary chat-oauth-authorize-btn" data-server-name="' + safe + '"'
        + (authorized ? " disabled" : "") + '>'
        + (authorized ? "Authorized \u2713" : "Authorize") + '</button>'
        + '</div>';
      rowsContainer.insertAdjacentHTML("beforeend", rowHtml);
    });
  }

  function _showOAuthGatePanel(sessionId, secretKey, servers, replayTask, replayAttachmentIds) {
    setRunningState(false);
    _teardownOAuthGate();

    chatMessages.querySelectorAll(".chat-status-badge, .chat-restart-panel, .chat-oauth-panel, .chat-remote-panel").forEach(function (el) {
      el.remove();
    });

    var projectId = _resolveProjectId();

    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-oauth-panel"'
      + ' data-session-id="' + escapeHtml(sessionId) + '"'
      + ' data-project-id="' + escapeHtml(projectId) + '">'
      + '<div class="chat-oauth-panel__title">&#x1F510; MCP authorization required</div>'
      + '<p class="chat-oauth-panel__hint">One or more MCP servers used by this project require OAuth authorization for this session. Authorize each server below; the run will resume automatically once all are connected.</p>'
      + '<div class="chat-oauth-panel__rows"><p class="chat-oauth-panel__loading">Connecting\u2026</p></div>'
      + '</div>'
    );

    var panel = chatMessages.querySelector(".chat-oauth-panel");
    if (!panel) return;
    panel._replayTask = replayTask || "";
    panel._replayAttachmentIds = (replayAttachmentIds || []).slice();
    // Stash server list for postMessage path before WS connects.
    panel._pendingServers = (servers || []).slice();
    chatMessages.scrollTop = chatMessages.scrollHeight;

    _attachOAuthGateBehavior(panel, sessionId, secretKey);
  }

  function _attachOAuthGateBehavior(panel, sessionId, secretKey) {
    function _markRowAuthorized(name) {
      var row = panel.querySelector('.chat-oauth-panel__row[data-server-name="' + (window.CSS && CSS.escape ? CSS.escape(name) : name) + '"]');
      if (!row) return;
      row.classList.add("chat-oauth-panel__row--authorized");
      var status = row.querySelector(".chat-oauth-panel__status");
      if (status) {
        status.classList.remove("chat-oauth-panel__status--pending");
        status.classList.add("chat-oauth-panel__status--authorized");
        status.textContent = "Authorized \u2713";
      }
      var btn = row.querySelector(".chat-oauth-authorize-btn");
      if (btn) { btn.disabled = true; }
    }

    function _allAuthorized() {
      var rows = panel.querySelectorAll(".chat-oauth-panel__row");
      if (!rows.length) return false;
      for (var i = 0; i < rows.length; i++) {
        if (!rows[i].classList.contains("chat-oauth-panel__row--authorized")) return false;
      }
      return true;
    }

    function _onAllAuthorized() {
      _teardownOAuthGate();
      var task = panel._replayTask || "";
      var ids = panel._replayAttachmentIds || [];
      panel.remove();
      // Re-issue the run; server will gate again if anything is still missing.
      _doStartRun(sessionId, secretKey, task, ids);
      setRunningState(true);
    }

    // postMessage from popup callback page — instant feedback when popup is open.
    _oauthMessageListener = function (event) {
      var data = event.data || {};
      if (data.type === "mcp_oauth_done" && data.success && data.server_name) {
        // Ensure rows are rendered if WS state hasn't arrived yet.
        if (panel._pendingServers && panel._pendingServers.length) {
          _renderOAuthRows(panel, panel._pendingServers);
          panel._pendingServers = [];
        }
        _markRowAuthorized(data.server_name);
        if (_allAuthorized()) _onAllAuthorized();
      }
    };
    window.addEventListener("message", _oauthMessageListener);

    // WebSocket connection for server-push readiness updates.
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = proto + "//" + location.host + "/ws/mcp/oauth/" + encodeURIComponent(sessionId)
      + "/?skey=" + encodeURIComponent(secretKey);
    try {
      _oauthWs = new WebSocket(wsUrl);
    } catch (_) {
      _oauthWs = null;
    }

    if (_oauthWs) {
      _oauthWs.onmessage = function (event) {
        var msg;
        try { msg = JSON.parse(event.data); } catch (_) { return; }
        if (!document.body.contains(panel)) { _teardownOAuthGate(); return; }

        if (msg.type === "state") {
          // Full initial state: render all rows with their current authorized state.
          _renderOAuthRows(panel, msg.servers || []);
          panel._pendingServers = [];
        } else if (msg.type === "update") {
          // A server was just authorized: ensure rows exist then mark it.
          if (panel._pendingServers && panel._pendingServers.length) {
            _renderOAuthRows(panel, panel._pendingServers);
            panel._pendingServers = [];
          }
          _markRowAuthorized(msg.server_name);
        } else if (msg.type === "complete") {
          _onAllAuthorized();
        }
      };
      _oauthWs.onerror = function () {
        // WS unavailable — postMessage path remains active for popup scenarios.
        // Render rows from stash so the user can still click Authorize.
        if (panel._pendingServers && panel._pendingServers.length) {
          _renderOAuthRows(panel, panel._pendingServers);
          panel._pendingServers = [];
        }
      };
      _oauthWs.onclose = function () {
        if (_oauthWs) { _oauthWs = null; }
      };
    }
  }

  // Delegated click handler for Authorize buttons (works for server-rendered
  // panels after page reload as well as JS-injected panels).
  document.addEventListener("click", function (event) {
    var btn = event.target && event.target.closest ? event.target.closest(".chat-oauth-authorize-btn") : null;
    if (!btn) return;
    var panel = btn.closest(".chat-oauth-panel");
    if (!panel) return;
    var sessionId = panel.dataset.sessionId
      || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var projectId = panel.dataset.projectId || _resolveProjectId();
    var secretKey = getSecretKey();
    if (!sessionId || !secretKey) {
      alert("Enter the Secret Key first.");
      return;
    }
    var serverName = btn.dataset.serverName;
    if (window.McpOAuth) window.McpOAuth.openAuthPopup(serverName, sessionId, projectId, secretKey);
    // Wire WS + postMessage once on first click for a server-rendered panel.
    if (!_oauthMessageListener) _attachOAuthGateBehavior(panel, sessionId, secretKey);
  });

  // ------------------------------------------------------------------
  // Remote-user in-history readiness panel
  // ------------------------------------------------------------------

  var _remoteUserWs = null;

  function _teardownRemoteUserGate() {
    if (_remoteUserWs) {
      try { _remoteUserWs.close(); } catch (_) {}
      _remoteUserWs = null;
    }
  }

  function _showRemoteUserGatePanel(sessionId, secretKey, users, quorum, replayTask, replayAttachmentIds) {
    setRunningState(false);
    _teardownRemoteUserGate();

    chatMessages.querySelectorAll(".chat-status-badge, .chat-restart-panel, .chat-oauth-panel, .chat-remote-panel").forEach(function (el) {
      el.remove();
    });

    var projectId = _resolveProjectId();

    chatMessages.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-remote-panel"'
      + ' data-session-id="' + escapeHtml(sessionId) + '"'
      + ' data-project-id="' + escapeHtml(projectId) + '"'
      + ' data-quorum="' + escapeHtml(quorum || "na") + '">'
      + '<div class="chat-remote-panel__title">&#x1F465; Waiting for remote participants</div>'
      + '<p class="chat-remote-panel__hint">Share the invite link with each participant; the run will resume automatically once all required users are online.</p>'
      + '<div class="chat-remote-panel__rows"><p class="chat-remote-panel__loading">Connecting\u2026</p></div>'
      + '<div class="chat-remote-panel__footer"></div>'
      + '</div>'
    );

    var panel = chatMessages.querySelector(".chat-remote-panel");
    if (!panel) return;
    panel._replayTask = replayTask || "";
    panel._replayAttachmentIds = (replayAttachmentIds || []).slice();
    panel._users = (users || []).slice();
    chatMessages.scrollTop = chatMessages.scrollHeight;

    _attachRemoteUserGateBehavior(panel, sessionId, secretKey);
  }

  function _teardownHostSessionWs() {
    if (_hostSessionWs) {
      try { _hostSessionWs.close(); } catch (_) {}
      _hostSessionWs = null;
    }
  }

  function _connectHostSessionWs(sessionId, secretKey) {
    if (!sessionId || !secretKey) return;
    _teardownHostSessionWs();
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = proto + "//" + location.host + "/ws/session/" + encodeURIComponent(sessionId)
      + "/?skey=" + encodeURIComponent(secretKey);
    try {
      _hostSessionWs = new WebSocket(wsUrl);
    } catch (_) {
      _hostSessionWs = null;
      return;
    }

    _hostSessionWs.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }
      if (!msg) return;

      if (msg.type === "message") {
        var payload = msg.message || msg;
        if (!payload || payload.role !== "user") return;
        if (payload.user_origin === "host") return;
        appendRemoteUserBubble(payload);
        return;
      }

      if (msg.type === "quorum_progress") {
        if (_gateData && _gateData.quorum === "all") {
          _gateData.awaiting_host_final = !!msg.all_present;
          _evalSendBtn();
        }
        return;
      }

      if (msg.type === "quorum_committed" && _gateData) {
        var shouldAutoStart = msg.quorum === "first_win" && !_activeReader;
        _gateData.awaiting_host_final = false;
        _evalSendBtn();
        if (shouldAutoStart) {
          clearGateMode();
          startRun("", []);
        }
      }
    };

    _hostSessionWs.onclose = function () {
      _hostSessionWs = null;
    };
  }

  function _renderRemoteUserRows(panel, users, quorum, secretKey) {
    var rowsContainer = panel.querySelector(".chat-remote-panel__rows");
    if (!rowsContainer) return;
    var loading = rowsContainer.querySelector(".chat-remote-panel__loading");
    if (loading) loading.remove();

    var sessionId = panel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var effectiveQuorum = quorum || panel.dataset.quorum || "na";
    var isTeamChoice = effectiveQuorum === "team_choice";

    (users || []).forEach(function (u) {
      var name = typeof u === "string" ? u : (u.name || "");
      var status = typeof u === "object" ? (u.status || "offline") : "offline";

      if (rowsContainer.querySelector('[data-remote-user-name="' + name.replace(/"/g, '\\"') + '"]')) return;

      var safe = escapeHtml(name);
      var statusClass = "remote-user-status--" + (status === "online" ? "online" : status === "ignored" ? "ignored" : "offline");
      var statusLabel = status === "online" ? "Online \u2713" : status === "ignored" ? "Ignore" : "Offline";
      var cbDisabled = isTeamChoice ? " disabled" : "";
      var copyDisabled = (status === "ignored") ? " disabled" : "";

      var rowHtml = '<div class="remote-user-row" data-remote-user-name="' + safe + '" data-status="' + escapeHtml(status) + '">'
        + '<input type="checkbox" class="remote-user-row__checkbox" title="Require this user" checked' + cbDisabled + '>'
        + '<span class="remote-user-row__name">' + safe + '</span>'
        + '<span class="remote-user-row__status ' + statusClass + '">' + statusLabel + '</span>'
        + '<button type="button" class="btn btn--sm btn--secondary remote-user-copy-btn" data-session-id="' + escapeHtml(sessionId) + '" data-user-name="' + safe + '" title="Copy invite link"' + copyDisabled + '>Copy Link</button>'
        + '</div>';
      rowsContainer.insertAdjacentHTML("beforeend", rowHtml);
    });
  }

  function _renderQuorumDropdown(panel, quorum) {
    var footer = panel.querySelector(".chat-remote-panel__footer");
    if (!footer) return;

    // Normalise: treat missing/legacy 'na' as 'all'.
    var effectiveQuorum = (!quorum || quorum === "na") ? "all" : quorum;
    var isTeamChoice = effectiveQuorum === "team_choice";

    // Build options from the single-source list injected by the server (util.QUORUM_OPTIONS → window._quorumOptions).
    var allOptions = window._quorumOptions;
    if (!allOptions || !allOptions.length) return; // server should always inject this

    // 'team_choice' is only selectable via Project Config; hide it from the live dropdown
    // unless the project is already configured with that value.
    var visibleOptions = isTeamChoice
      ? allOptions
      : allOptions.filter(function (o) { return o.value !== "team_choice"; });

    // Idempotent: if dropdown already exists, update value, disabled state, and option set.
    var existingSel = footer.querySelector(".remote-panel-quorum-select");
    if (existingSel) {
      // Remove team_choice option if it shouldn't be visible.
      if (!isTeamChoice) {
        var tcOpt = existingSel.querySelector('option[value="team_choice"]');
        if (tcOpt) tcOpt.remove();
      }
      existingSel.value = effectiveQuorum;
      existingSel.disabled = isTeamChoice;
      return;
    }

    var sessionId = panel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var optionsHtml = visibleOptions.map(function (opt) {
      return '<option value="' + escapeHtml(opt.value) + '"'
        + (opt.value === effectiveQuorum ? " selected" : "") + ">"
        + escapeHtml(opt.label) + "</option>";
    }).join("");

    var html = '<label class="remote-panel-quorum-label">Quorum:'
      + '<select class="remote-panel-quorum-select" data-session-id="' + escapeHtml(sessionId) + '"'
      + (isTeamChoice ? " disabled" : "") + ">"
      + optionsHtml
      + "</select></label>";
    footer.insertAdjacentHTML("afterbegin", html);
  }

  function _updateRemoteUserRow(panel, userName, status) {
    var row = panel.querySelector('[data-remote-user-name="' + (window.CSS && CSS.escape ? CSS.escape(userName) : userName) + '"]');
    if (!row) return;
    row.dataset.status = status;
    var statusEl = row.querySelector(".remote-user-row__status");
    if (statusEl) {
      statusEl.className = "remote-user-row__status remote-user-status--" + (status === "online" ? "online" : status === "ignored" ? "ignored" : "offline");
      statusEl.textContent = status === "online" ? "Online \u2713" : status === "ignored" ? "Ignore" : "Offline";
    }
    // Copy link button: disabled when ignored so the token has been revoked.
    var copyBtn = row.querySelector(".remote-user-copy-btn");
    if (copyBtn) {
      copyBtn.disabled = (status === "ignored");
    }
    // Checkbox: sync visual state but respect disabled (team_choice) rows.
    var cb = row.querySelector(".remote-user-row__checkbox");
    if (cb && !cb.disabled) {
      cb.checked = (status !== "ignored");
    }
  }

  function _attachRemoteUserGateBehavior(panel, sessionId, secretKey) {
    function _onAllReady() {
      _teardownRemoteUserGate();
      var task = panel._replayTask || "";
      var ids = panel._replayAttachmentIds || [];
      panel.remove();
      _doStartRun(sessionId, secretKey, task, ids);
      setRunningState(true);
    }

    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = proto + "//" + location.host + "/ws/remote-users/" + encodeURIComponent(sessionId)
      + "/?skey=" + encodeURIComponent(secretKey);
    try {
      _remoteUserWs = new WebSocket(wsUrl);
    } catch (_) {
      _remoteUserWs = null;
    }

    if (_remoteUserWs) {
      _remoteUserWs.onmessage = function (event) {
        var msg;
        try { msg = JSON.parse(event.data); } catch (_) { return; }
        if (!document.body.contains(panel)) { _teardownRemoteUserGate(); return; }

        var quorum = msg.quorum || panel.dataset.quorum || "na";
        if (msg.type === "state") {
          // Update panel quorum from server (may reflect Redis override).
          panel.dataset.quorum = quorum;
          _renderRemoteUserRows(panel, msg.users || [], quorum, secretKey);
          _renderQuorumDropdown(panel, quorum);
          panel._users = msg.users || [];
        } else if (msg.type === "update") {
          if (panel._users && !panel._users.some(function (u) { return u.name === msg.user_name; })) {
            panel._users.push({name: msg.user_name, status: msg.status});
            _renderRemoteUserRows(panel, [{name: msg.user_name, status: msg.status}], quorum, secretKey);
          } else {
            // Update user_type on existing entry too.
            if (panel._users) {
              panel._users.forEach(function (u) {
                if (u.name === msg.user_name) { u.status = msg.status; }
              });
            }
            _updateRemoteUserRow(panel, msg.user_name, msg.status);
          }
        } else if (msg.type === "count_update" || msg.type === "complete") {
          if (msg.type === "complete") _onAllReady();
        }
      };
      _remoteUserWs.onerror = function () {
        if (panel._users && panel._users.length) {
          _renderRemoteUserRows(panel, panel._users, panel.dataset.quorum || "na", secretKey);
        }
      };
      _remoteUserWs.onclose = function () {
        if (_remoteUserWs) { _remoteUserWs = null; }
      };
    }
  }

  // Delegated: Copy invite link button
  document.addEventListener("click", function (event) {
    var btn = event.target && event.target.closest ? event.target.closest(".remote-user-copy-btn") : null;
    if (!btn || btn.disabled) return;
    var sessionId = btn.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var userName = btn.dataset.userName;
    var secretKey = getSecretKey();
    if (!sessionId || !userName || !secretKey) { alert("Enter the Secret Key first."); return; }

    fetch("/chat/sessions/" + sessionId + "/remote-users/" + encodeURIComponent(userName) + "/invite/", {
      method: "POST",
      headers: {"X-App-Secret-Key": secretKey, "X-CSRFToken": csrfToken},
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.join_url) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(d.join_url).then(function () {
            var orig = btn.textContent;
            btn.textContent = "Copied!";
            setTimeout(function () { btn.textContent = orig; }, 2000);
          }).catch(function () { alert(d.join_url); });
        } else { alert(d.join_url); }
      } else { alert(d.error || "Failed to generate link."); }
    }).catch(function () { alert("Error generating invite link."); });
  });

  // Delegated: checkbox toggle (ignore/unignore)
  document.addEventListener("change", function (event) {
    var cb = event.target && event.target.classList && event.target.classList.contains("remote-user-row__checkbox") ? event.target : null;
    if (!cb) return;
    var row = cb.closest(".remote-user-row");
    var panel = cb.closest(".chat-remote-panel");
    if (!row || !panel) return;
    var sessionId = panel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var userName = row.dataset.remoteUserName;
    var secretKey = getSecretKey();
    if (!sessionId || !userName || !secretKey) return;

    // Sync copy link button state immediately for responsive UI.
    var copyBtn = row.querySelector(".remote-user-copy-btn");
    if (copyBtn) copyBtn.disabled = !cb.checked;

    var endpoint = cb.checked ? "unignore" : "ignore";
    fetch("/chat/sessions/" + sessionId + "/remote-users/" + encodeURIComponent(userName) + "/" + endpoint + "/", {
      method: "POST",
      headers: {"X-App-Secret-Key": secretKey, "X-CSRFToken": csrfToken},
    }).catch(function () { /* non-fatal */ });
  });

  // Delegated: Quorum dropdown change
  document.addEventListener("change", function (event) {
    var sel = event.target && event.target.classList && event.target.classList.contains("remote-panel-quorum-select") ? event.target : null;
    if (!sel) return;
    var sessionId = sel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var secretKey = getSecretKey();
    if (!sessionId || !secretKey) return;
    var quorum = sel.value;
    var body = new URLSearchParams({quorum: quorum});
    fetch("/chat/sessions/" + sessionId + "/remote-users/quorum/", {
      method: "POST",
      headers: {"X-App-Secret-Key": secretKey, "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded"},
      body: body.toString(),
    }).catch(function () { /* non-fatal */ });
  });

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

  function startRun(task, attachmentIds) {
    var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
    if (!sessionId) { return; }

    var secretKey = getSecretKey();
    if (!secretKey) { alert("Enter the Secret Key first."); return; }

    chatMessages.querySelectorAll(".chat-status-badge, .chat-restart-panel, .chat-oauth-panel, .chat-remote-panel").forEach(function (el) {
      el.remove();
    });

    setRunningState(true);

    // OAuth gate is now driven by the server: /run/ returns 409 +
    // {status:"awaiting_mcp_oauth", servers:[...]} when authorization is needed,
    // and the SSE stream may also emit `awaiting_mcp_oauth` mid-run. Both paths
    // are handled inside _doStartRun.
    _doStartRun(sessionId, secretKey, task, attachmentIds);
  }

  function _doStartRun(sessionId, secretKey, task, attachmentIds) {
    var body = new URLSearchParams();
    body.append("task", task || "");
    (attachmentIds || []).forEach(function (id) {
      if (id) body.append("attachment_ids", id);
    });

    return fetch("/chat/sessions/" + sessionId + "/run/", {
      method: "POST",
      headers: {
        "X-App-Secret-Key": secretKey,
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
    }).then(function (response) {
      if (response.status === 409) {
        // Either awaiting_mcp_oauth (our gate) or "already running"/"changed".
        // Distinguish by JSON body.
        return response.json().then(function (d) {
          if (d && d.status === "awaiting_mcp_oauth") {
            _showOAuthGatePanel(sessionId, secretKey, d.servers || [], task, attachmentIds);
            return;
          }
          if (d && d.status === "awaiting_remote_users") {
            _showRemoteUserGatePanel(sessionId, secretKey, d.users || [], d.quorum || "na", task, attachmentIds);
            return;
          }
          throw new Error(d.error || "Run failed");
        });
      }
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
            // If a gate event was already processed, the input bar is in gate
            // mode — do not call setRunningState(false) and re-hide the Stop button.
            if (!_gateData) setRunningState(false);
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
          if (!_gateData) setRunningState(false);
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
        '<div class="chat-bubble chat-bubble--ai" data-raw-content="' + escapeHtml(data.content || "") + '">'
        + '<div class="chat-bubble__avatar">' + initial + '</div>'
        + '<div class="chat-bubble__body">'
        + '<div class="chat-bubble__meta">'
        + '<span class="chat-bubble__name">' + (data.agent_name || "Agent") + '</span>'
        + '<span class="chat-bubble__time"><time class="local-time" data-utc="' + ts + '">' + ts + '</time></span>'
        + _buildCopyBtn()
        + '</div>'
        + '<div class="chat-bubble__content">' + contentHtml + '</div>'
        + attachmentsHtml
        + exportHtml
        + '</div></div>'
      );
      window.renderLocalTimes();
    } else if (eventName === "gate") {
      setRunningState(false);
      appendGateBadge(data);
      setGateMode(data);
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
    } else if (eventName === "awaiting_mcp_oauth") {
      // Mid-run token expiry: surface the same in-history authorization card.
      var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      var secretKey = getSecretKey();
      if (sessionId && secretKey) {
        _showOAuthGatePanel(sessionId, secretKey, data.servers || [], "", []);
      }
    }
  }

  if (chatSendBtn) {
    chatSendBtn.addEventListener("click", function () {
      if (chatSendBtn.disabled) return;
      // Fork to gate handler when the input bar is in human-gate mode.
      if (_gateData) { _handleGateSend(); return; }
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
      // If a restart panel is visible the session is stopped/completed.
      // Typing in the send box should start a fresh session, not resume the old one.
      if (sessionId && chatMessages && chatMessages.querySelector(".chat-restart-panel")) {
        sessionId = "";
      }
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
      if (chatStopBtn.disabled) return;
      var sessionId = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      var secretKey = getSecretKey();
      if (!sessionId || !secretKey) return;
      // Disable immediately to prevent double-click.
      chatStopBtn.disabled = true;
      if (_gateData) {
        // Gate mode: use the respond endpoint so the backend transitions the
        // session from awaiting_input → stopped cleanly.
        sendRespond(sessionId, "stop", "").then(function () {
          clearGateMode();
          appendStatusBadge("stopped");
          appendRestartPanel();
        }).catch(function (err) {
          chatStopBtn.disabled = false;
          appendBubble('<div class="chat-bubble chat-bubble--error">Error: ' + err.message + '</div>');
        });
      } else {
        // Active run: fire-and-forget signal to the run endpoint.
        fetch("/chat/sessions/" + sessionId + "/stop/", {
          method: "POST",
          headers: { "X-App-Secret-Key": secretKey, "X-CSRFToken": csrfToken },
        });
        // setRunningState(false) is called when the SSE "stopped" event arrives,
        // which hides the button — disabled state is the interim guard.
      }
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
    // Restore gate mode if the newly loaded session is awaiting_input.
    // The server renders a .chat-status-badge--gate with data-gate-context
    // so we can reconstruct the gate state without an extra API call.
    var gateBadge = chatMessages
      && chatMessages.querySelector(".chat-status-badge--gate[data-gate-context]");
    if (gateBadge) {
      try {
        var ctx = JSON.parse(gateBadge.dataset.gateContext);
        setGateMode(ctx);
      } catch (e) {
        clearGateMode();
      }
    } else {
      clearGateMode();
    }
    // Reconnect WS for server-rendered awaiting_mcp_oauth panels (session switch).
    var oauthPanel = chatMessages && chatMessages.querySelector(".chat-oauth-panel");
    if (oauthPanel && !_oauthMessageListener) {
      var oauthSessionId = oauthPanel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
      var oauthSecretKey = getSecretKey();
      if (oauthSessionId && oauthSecretKey) {
        _attachOAuthGateBehavior(oauthPanel, oauthSessionId, oauthSecretKey);
      }
    }
    // Reconnect WS for server-rendered awaiting_remote_users panels (session switch).
    var remotePanel = chatMessages && chatMessages.querySelector(".chat-remote-panel");
    if (remotePanel && !_remoteUserWs) {
      var remotePanelSessionId = remotePanel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
      var remotePanelSecretKey = getSecretKey();
      if (remotePanelSessionId && remotePanelSecretKey) {
        // Render the quorum dropdown immediately from data-quorum (server-injected);
        // the WS state message will update it once connected.
        _renderQuorumDropdown(remotePanel, remotePanel.dataset.quorum || "na");
        _attachRemoteUserGateBehavior(remotePanel, remotePanelSessionId, remotePanelSecretKey);
      }
    }
    var sid = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
    var skey = getSecretKey();
    if (sid && skey) {
      _connectHostSessionWs(sid, skey);
    } else {
      _teardownHostSessionWs();
    }
  });

  document.body.addEventListener("input", function (e) {
    if (e.target.id === "global-secret-key") {
      updateChatAuthState();
      var sid = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
      var skey = getSecretKey();
      if (sid && skey) {
        _connectHostSessionWs(sid, skey);
      } else {
        _teardownHostSessionWs();
      }
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

  // On initial page load, restore gate mode if the session is awaiting_input.
  // htmx:afterSwap covers session switches; this covers first render.
  var initialGateBadge = chatMessages
    && chatMessages.querySelector(".chat-status-badge--gate[data-gate-context]");
  if (initialGateBadge) {
    try {
      setGateMode(JSON.parse(initialGateBadge.dataset.gateContext));
    } catch (e) { /* malformed JSON — ignore */ }
  }

  // On initial page load, connect WS for server-rendered awaiting_mcp_oauth panels.
  var initialOAuthPanel = chatMessages && chatMessages.querySelector(".chat-oauth-panel");
  if (initialOAuthPanel) {
    var initOAuthSessionId = initialOAuthPanel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var initOAuthSecretKey = getSecretKey();
    if (initOAuthSessionId && initOAuthSecretKey) {
      _attachOAuthGateBehavior(initialOAuthPanel, initOAuthSessionId, initOAuthSecretKey);
    }
  }
  // On initial page load, connect WS for server-rendered awaiting_remote_users panels.
  var initialRemotePanel = chatMessages && chatMessages.querySelector(".chat-remote-panel");
  if (initialRemotePanel) {
    var initRemoteSessionId = initialRemotePanel.dataset.sessionId || (activeSessionIdInput ? activeSessionIdInput.value.trim() : "");
    var initRemoteSecretKey = getSecretKey();
    if (initRemoteSessionId && initRemoteSecretKey) {
      // Render the quorum dropdown immediately from data-quorum (server-injected);
      // the WS state message will update it once connected.
      _renderQuorumDropdown(initialRemotePanel, initialRemotePanel.dataset.quorum || "na");
      _attachRemoteUserGateBehavior(initialRemotePanel, initRemoteSessionId, initRemoteSecretKey);
    }
  }
  var initSid = activeSessionIdInput ? activeSessionIdInput.value.trim() : "";
  var initSecret = getSecretKey();
  if (initSid && initSecret) {
    _connectHostSessionWs(initSid, initSecret);
  }
});
