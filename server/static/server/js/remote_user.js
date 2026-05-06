/**
 * remote_user.js â€” Remote-user standalone chat page
 *
 * Responsibilities:
 * 1. On load: POST /remote/join/<token>/online/ to mark user as online.
 * 2. Open WebSocket to /ws/remote/chat/<token>/ for live agent messages.
 * 3. Inject history via WS when no server-side discussions were rendered.
 * 4. Append new agent messages as they arrive.
 * 5. Allow the remote user to type and send messages (displayed locally).
 * 6. Show an eviction overlay when ignored by the host.
 *
 * CSS reuse: this page uses the shared .chat-bubble--human / .chat-bubble--ai
 * and .chat-input-panel / .chat-input-row / .chat-input__textarea classes that
 * are defined in main.scss for the home chat panel. No new styles are needed.
 */
(function () {
  "use strict";

  var token = window._remoteUserToken || "";
  var userName = window._remoteUserName || "You";
  if (!token) return;

  var _ws = null;
  var _seenMessageIds = Object.create(null);
  var composePendingFiles = [];
  var composeUploaded = [];
  var _composerState = "waiting_turn";
  var _currentGateData = null;

  // --------------------------------------------------------------------------
  // Helpers
  // --------------------------------------------------------------------------
  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Render markdown using marked.js (loaded by page via CDN). */
  function renderMd(text) {
    if (window.marked && typeof window.marked.parse === "function") {
      try { return window.marked.parse(text || ""); } catch (_) {}
    }
    return escapeHtml(text || "");
  }

  function renderLocalTimes() {
    document.querySelectorAll(".local-time[data-utc]:not([data-rendered])").forEach(function (el) {
      var d = new Date(el.dataset.utc);
      if (!isNaN(d.getTime())) {
        el.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        el.dataset.rendered = "1";
      }
    });
  }

  function scrollToBottom() {
    var c = document.getElementById("remote-chat-messages");
    if (c) c.scrollTop = c.scrollHeight;
  }

  function _getHistoryContainer() {
    var container = document.getElementById("remote-chat-messages");
    if (!container) return null;
    var history = document.getElementById("remote-chat-history-msgs");
    if (history) return history;
    history = document.createElement("div");
    history.className = "chat-history";
    history.id = "remote-chat-history-msgs";
    container.insertAdjacentElement("afterbegin", history);
    return history;
  }

  function buildCopyBtn() {
    return window.ChatCopyUtils.buildCopyBtnHtml();
  }

  function _roundLabel(data) {
    var d = data || {};
    var round = Number(d.round ?? 0);
    var maxRounds = Number(d.max_rounds ?? 0);
    if (!isFinite(round) || round <= 0) return "";
    if (isFinite(maxRounds) && maxRounds > 0) return "Round " + round + "/" + maxRounds;
    return "Round " + round;
  }

  function _clearGateBadge() {
    var c = document.getElementById("remote-chat-messages");
    if (!c) return;
    var badge = c.querySelector(".chat-status-badge--gate");
    if (badge) badge.remove();
  }

  function _clearTerminalStatusBadges() {
    var c = document.getElementById("remote-chat-messages");
    if (!c) return;
    c.querySelectorAll(".chat-status-badge--stopped, .chat-status-badge--completed").forEach(function (el) {
      el.remove();
    });
  }

  function _appendTerminalStatusBadge(type) {
    var c = document.getElementById("remote-chat-messages");
    if (!c) return;

    var label = type === "completed" ? "Run completed" : "Run stopped";
    _setAgentsWorkingBadge(false);
    _clearTerminalStatusBadges();
    c.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-status-badge chat-status-badge--' + type + '">' + label + "</div>"
    );
    scrollToBottom();
  }

  function _setAgentsWorkingBadge(show) {
    var c = document.getElementById("remote-chat-messages");
    if (!c) return;

    var runningBadges = c.querySelectorAll(".chat-status-badge--running");
    var badge = runningBadges.length ? runningBadges[0] : null;
    if (runningBadges.length > 1) {
      Array.prototype.slice.call(runningBadges, 1).forEach(function (el) { el.remove(); });
    }

    if (!show) {
      if (badge) badge.remove();
      return;
    }

    _clearTerminalStatusBadges();
    if (badge) return;
    c.insertAdjacentHTML("beforeend", '<div class="chat-status-badge chat-status-badge--running">\u2699 Agents at work</div>');
    scrollToBottom();
  }

  function _syncSendEnabled() {
    var sendBtn = document.getElementById("remote-send-btn");
    var input = document.getElementById("remote-chat-input");
    if (!sendBtn) return;
    if (_composerState !== "active_turn") {
      sendBtn.disabled = true;
      return;
    }
    var hasText = !!(input && input.value.trim());
    var hasAttachments = composePendingFiles.length > 0 || composeUploaded.length > 0;
    sendBtn.disabled = !(hasText || hasAttachments);
  }

  function _setComposerState(nextState, gateData) {
    var input = document.getElementById("remote-chat-input");
    var sendBtn = document.getElementById("remote-send-btn");
    var attachBtn = document.getElementById("remote-attach-btn");
    var attachInput = document.getElementById("remote-attach-input");
    var inputRow = input && input.closest ? input.closest(".chat-input-row") : null;

    function setTurnCue(active, text) {
      var panel = input && input.closest ? input.closest(".chat-input-panel") : null;
      var hint = panel ? panel.querySelector(".chat-turn-hint") : null;
      if (!hint && panel) {
        hint = document.createElement("div");
        hint.className = "chat-turn-hint";
        hint.hidden = true;
        panel.appendChild(hint);
      }
      if (inputRow) {
        inputRow.classList.toggle("chat-input-row--active-turn", !!active);
      }
      if (hint) {
        hint.hidden = !active;
        hint.textContent = active ? (text || "Your turn - type a response") : "";
      }
      if (!active || !inputRow) return;
      inputRow.classList.remove("chat-input-row--active-turn-pulse");
      void inputRow.offsetWidth;
      inputRow.classList.add("chat-input-row--active-turn-pulse");
      if (input) input.focus();
      setTimeout(function () {
        if (inputRow) inputRow.classList.remove("chat-input-row--active-turn-pulse");
      }, 1200);
    }

    _composerState = nextState;
    if (gateData) _currentGateData = gateData;

    if (nextState === "active_turn") {
      var roundText = _roundLabel(_currentGateData || {});
      if (input) {
        input.disabled = false;
        input.placeholder = roundText ? (roundText + " - enter your response...") : "enter your response...";
      }
      if (attachBtn) attachBtn.disabled = false;
      if (attachInput) attachInput.disabled = false;
      setTurnCue(true, "Your turn - enter your response");
      _syncSendEnabled();
      return;
    }

    if (nextState === "sending") {
      if (input) {
        input.disabled = true;
        input.placeholder = "Sending response...";
      }
      if (attachBtn) attachBtn.disabled = true;
      if (attachInput) attachInput.disabled = true;
      if (sendBtn) sendBtn.disabled = true;
      setTurnCue(false, "");
      return;
    }

    if (input) {
      input.disabled = true;
      input.placeholder = "wait for your turn";
    }
    if (attachBtn) attachBtn.disabled = true;
    if (attachInput) attachInput.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
    setTurnCue(false, "");
  }

  function _setAwaitingTurn(data) {
    _setAgentsWorkingBadge(false);
    _clearTerminalStatusBadges();
    _setComposerState("active_turn", data);
  }

  function _setWaitingTurn() {
    _clearGateBadge();
    _setComposerState("waiting_turn");
  }

  function _applyRunStatus(msg) {
    var status = (msg && (msg.status || msg.event)) || "";
    if (!status) return;

    if (status === "running") {
      _setAgentsWorkingBadge(true);
      _setWaitingTurn();
      return;
    }

    if (status === "awaiting_input") {
      if ((msg.quorum || "") === "team_choice") {
        _setWaitingTurn();
        return;
      }
      _setAwaitingTurn(msg);
      return;
    }

    if (status === "stopped") {
      _setWaitingTurn();
      _appendTerminalStatusBadge("stopped");
      return;
    }

    if (status === "completed") {
      _setWaitingTurn();
      _appendTerminalStatusBadge("completed");
      return;
    }

    if (status === "idle") {
      _setAgentsWorkingBadge(false);
      _setWaitingTurn();
      return;
    }

    _setAgentsWorkingBadge(false);
    _setWaitingTurn();
  }

  function _initComposerState() {
    var main = document.getElementById("remote-chat-main");
    var status = main ? (main.dataset.sessionStatus || "") : "";
    var gate = window._remoteGateContext || null;

    if (status === "awaiting_input" && gate) {
      _setAwaitingTurn(gate);
      return;
    }
    if (status === "running") {
      _setAgentsWorkingBadge(true);
    }

    _setWaitingTurn();
  }

  // --------------------------------------------------------------------------
  // Bubble factories â€” identical DOM structure to chat_session_history.html
  // --------------------------------------------------------------------------

  /**
   * Build an agent bubble matching .chat-bubble--ai in chat_session_history.html.
   * @param {object} msg  {agent_name, content, timestamp}
   */
  function buildAssistantBubble(msg) {
    var agentName = msg.agent_name || "Agent";
    var content   = msg.content   || "";
    var ts        = msg.timestamp || "";
    var initial   = agentName.slice(0, 1).toUpperCase();

    var timeHtml = ts
      ? '<span class="chat-bubble__time"><time class="local-time" data-utc="'
          + escapeHtml(ts) + '"></time></span>'
      : "";

    var el = document.createElement("div");
    el.className = "chat-bubble chat-bubble--ai";
    el.dataset.rawContent = content;
    if (msg.providers) { el.dataset.exportProviders = JSON.stringify(msg.providers); }
    el.innerHTML =
      '<div class="chat-bubble__avatar">' + escapeHtml(initial) + "</div>"
      + '<div class="chat-bubble__body">'
      +   '<div class="chat-bubble__meta">'
      +     '<span class="chat-bubble__name">' + escapeHtml(agentName) + "</span>"
      +     timeHtml
      +     buildCopyBtn()
      +   "</div>"
      +   '<div class="chat-bubble__content"></div>'
      +   renderMessageAttachments(msg.attachments || [])
      +   _buildExportDropdownHtml(msg.providers, msg.discussion_id || "")
      + "</div>";

    el.querySelector(".chat-bubble__content").innerHTML = renderMd(content);
    return el;
  }

  // --------------------------------------------------------------------------
  // Export helpers
  // --------------------------------------------------------------------------
  function getExportKey() { return window._remoteExportKey || ""; }

  function _buildExportDropdownHtml(providers, discussionId) {
    if (!getExportKey() || !providers || !providers.length) return "";
    var sessionId = window._remoteSessionId || "";
    var html = '<div class="chat-bubble__actions">'
      + '<div class="export-dropdown" data-export-dropdown>'
      + '<button type="button" class="btn btn--sm btn--warning export-dropdown__toggle" aria-expanded="false">'
      + 'Export <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-left:3px"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'
      + '</button>'
      + '<div class="export-dropdown__menu" hidden>';
    (providers || []).forEach(function (p) {
      html += '<button type="button" class="export-dropdown__item"'
        + ' data-provider="' + escapeHtml(p.name) + '"'
        + ' data-session-id="' + escapeHtml(sessionId) + '"'
        + ' data-discussion-id="' + escapeHtml(discussionId || "") + '"'
        + '>' + escapeHtml(p.label) + '</button>';
    });
    html += '</div></div></div>';
    return html;
  }

  function openRemoteExportModal(provider, sessionId, discussionId) {
    var key = getExportKey();
    if (!key) { alert("Export access not granted."); return; }
    if (!window.ProviderRegistry || typeof window.ProviderRegistry.openExportModal !== "function") {
      alert("Export is not available.");
      return;
    }
    window.ProviderRegistry.openExportModal(provider, {
      provider: provider,
      sessionId: sessionId,
      discussionId: discussionId,
      secretKey: key,
      csrfToken: window._csrfToken || "",
      projectId: window._remoteProjectId || "",
    });
  }

  function _injectExportDropdowns() {
    var sessionId = window._remoteSessionId || "";
    document.querySelectorAll(".chat-bubble--ai").forEach(function (bubble) {
      if (bubble.querySelector(".chat-bubble__actions")) return;
      var body = bubble.querySelector(".chat-bubble__body");
      if (!body) return;
      var discussionId = bubble.dataset.discussionId || "";
      var rawProviders = bubble.dataset.exportProviders;
      var providers;
      try { providers = rawProviders ? JSON.parse(rawProviders) : null; } catch (_) { providers = null; }
      var html = _buildExportDropdownHtml(providers, discussionId);
      if (html) body.insertAdjacentHTML("beforeend", html);
    });
  }

  function _removeExportDropdowns() {
    document.querySelectorAll(".chat-bubble--ai .chat-bubble__actions").forEach(function (el) {
      el.remove();
    });
  }

  // Delegated click: export dropdown toggle and item
  document.addEventListener("click", function (event) {
    var toggle = event.target && event.target.closest ? event.target.closest(".export-dropdown__toggle") : null;
    if (toggle) {
      var menu = toggle.closest(".export-dropdown") && toggle.closest(".export-dropdown").querySelector(".export-dropdown__menu");
      if (menu) menu.hidden = !menu.hidden;
      return;
    }
    var item = event.target && event.target.closest ? event.target.closest(".export-dropdown__item") : null;
    if (item) {
      openRemoteExportModal(item.dataset.provider, item.dataset.sessionId, item.dataset.discussionId);
    }
  });

  /**
   * Build a user bubble matching .chat-bubble--human in chat_session_history.html.
   * @param {string} text  Raw message text.
   */
  function buildUserBubble(msg) {
    var senderName = msg.agent_name || "";
    var displayName = senderName && senderName === userName ? "You" : (senderName || userName);
    var content = msg.content || "";
    var ts = msg.timestamp || "";
    var timeHtml = ts
      ? '<span class="chat-bubble__time"><time class="local-time" data-utc="' + escapeHtml(ts) + '"></time></span>'
      : "";
    var el = document.createElement("div");
    el.className = "chat-bubble chat-bubble--human";
    el.dataset.rawContent = content;
    el.innerHTML =
      '<div class="chat-bubble__meta">'
      +   '<span class="chat-bubble__name">' + escapeHtml(displayName) + "</span>"
      +   timeHtml
      +   buildCopyBtn()
      + "</div>"
      + '<div class="chat-bubble__content"></div>'
      + renderMessageAttachments(msg.attachments || []);

    el.querySelector(".chat-bubble__content").innerHTML = renderMd(content);
    return el;
  }

  function _iconUrlForFile(filename) {
    var ext = (filename || "").split(".").pop().toLowerCase();
    var known = {
      pdf: 1, doc: 1, docx: 1, xls: 1, xlsx: 1, ppt: 1, pptx: 1,
      csv: 1, txt: 1, json: 1, xml: 1, md: 1,
    };
    var name = known[ext] ? ext : "document";
    return "/static/server/assets/icons/file-" + name + ".svg";
  }

  function renderMessageAttachments(attachments) {
    var list = attachments || [];
    if (!list.length) return "";
    var html = '<div class="chat-message-attachments">';
    list.forEach(function (att) {
      var name = escapeHtml(att.filename || "file");
      var url = att.content_url || "";
      var thumbUrl = att.thumbnail_url || (att.is_image ? url : _iconUrlForFile(att.filename || ""));
      var iconCls = att.is_image
        ? "chat-message-attachment__thumb"
        : "chat-message-attachment__thumb chat-message-attachment__thumb--icon";
      var thumb = thumbUrl
        ? '<img class="' + iconCls + '" src="' + thumbUrl + '" alt="' + name + '">'
        : "";
      html += '<a class="chat-message-attachment" href="' + url + '" target="_blank" rel="noopener noreferrer">'
        + thumb
        + '<span class="chat-message-attachment__name">' + name + "</span>"
        + "</a>";
    });
    html += "</div>";
    return html;
  }

  /** Append a pre-built bubble element into the messages container. */
  function appendBubble(el) {
    var history = _getHistoryContainer();
    if (!history) return;
    var waiting = history.querySelector(".remote-user-page__waiting");
    if (waiting) waiting.remove();
    history.appendChild(el);
    renderLocalTimes();
    scrollToBottom();
  }

  // --------------------------------------------------------------------------
  // Send message
  // --------------------------------------------------------------------------
  function sendMessage() {
    var input = document.getElementById("remote-chat-input");
    if (!input) return;
    if (_composerState !== "active_turn") return;
    var text = input.value.trim();
    var hasAttachments = composePendingFiles.length > 0 || composeUploaded.length > 0;
    if (!text && !hasAttachments) return;
    _setComposerState("sending");

    ensureComposeAttachmentsUploaded()
      .then(function (attachmentIds) {
        var body = new URLSearchParams();
        body.append("text", text);
        (attachmentIds || []).forEach(function (id) {
          if (id) body.append("attachment_ids", id);
        });
        return fetch("/remote/join/" + encodeURIComponent(token) + "/respond/", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: body.toString(),
        });
      })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) {
            var isGracefulConflict = r.status === 409 && (data.status === "locked" || data.status === "stale");
            if (!isGracefulConflict && r.status !== 202) {
              throw new Error(data.error || data.message || "Failed to send response");
            }
          }
          if (data.status === "locked" || data.status === "stale") {
            input.value = "";
            input.style.height = "";
            clearComposeAttachments();
            _setWaitingTurn();
            renderStatusNote(data.message || "Another participant already continued this run.");
            return data;
          }
          input.value = "";
          input.style.height = "";
          clearComposeAttachments();
          _setWaitingTurn();
          return data;
        });
      })
      .catch(function (err) {
        renderStatusNote(err.message || "Failed to send response.");
        _setComposerState("active_turn", _currentGateData || {});
      })
      .finally(function () {
        _syncSendEnabled();
      });
  }

  function renderStatusNote(text) {
    if (!text) return;
    var c = document.getElementById("remote-chat-messages");
    if (!c) return;
    c.insertAdjacentHTML(
      "beforeend",
      '<div class="chat-status-badge chat-status-badge--remote-users">\u23F3 ' + escapeHtml(text) + "</div>"
    );
    scrollToBottom();
  }

  function formatBytes(size) {
    var n = Number(size || 0);
    if (!n) return "0 B";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
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
      + '<span class="chat-attachment-chip__name">' + name + "</span>"
      + '<span class="chat-attachment-chip__meta">' + formatBytes(att.size_bytes) + "</span>"
      + closeTag
      + '<button class="chat-attachment-chip__remove" type="button" data-attachment-target="' + target + '" data-attachment-index="' + index + '">&#x00D7;</button>'
      + "</div>";
  }

  function renderComposeAttachments() {
    var attachList = document.getElementById("remote-compose-attachments");
    if (!attachList) return;
    var all = composeUploaded.concat(composePendingFiles);
    if (!all.length) {
      attachList.innerHTML = "";
      attachList.hidden = true;
      _syncSendEnabled();
      return;
    }
    attachList.hidden = false;
    attachList.innerHTML = all.map(function (att, idx) {
      return attachmentChipHtml(att, "compose", idx);
    }).join("");
    _syncSendEnabled();
  }

  function deleteUploadedAttachment(attachmentId) {
    return fetch(
      "/remote/join/" + encodeURIComponent(token) + "/attachments/" + encodeURIComponent(attachmentId) + "/delete/",
      { method: "POST" }
    ).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) {
          throw new Error((data && (data.error || data.message)) || "Failed to delete attachment");
        }
        return data;
      });
    });
  }

  function ensureComposeAttachmentsUploaded() {
    if (!composePendingFiles.length) {
      return Promise.resolve(composeUploaded.map(function (x) { return x.id; }));
    }
    var pending = composePendingFiles.slice();
    composePendingFiles = [];
    var form = new FormData();
    pending.forEach(function (rec) {
      if (rec && rec._file) form.append("files", rec._file);
    });
    return fetch("/remote/join/" + encodeURIComponent(token) + "/attachments/", {
      method: "POST",
      body: form,
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || "Attachment upload failed");
        composeUploaded = composeUploaded.concat(data.attachments || []);
        renderComposeAttachments();
        return composeUploaded.map(function (x) { return x.id; });
      });
    }).catch(function (err) {
      composePendingFiles = pending.concat(composePendingFiles);
      renderComposeAttachments();
      throw err;
    });
  }

  function clearComposeAttachments() {
    composePendingFiles = [];
    composeUploaded = [];
    renderComposeAttachments();
  }

  function addComposeFiles(fileList) {
    Array.from(fileList || []).forEach(function (file) {
      composePendingFiles.push(toUploadRecord(file));
    });
    renderComposeAttachments();
  }

  // --------------------------------------------------------------------------
  // Textarea: auto-grow + keyboard shortcut
  // --------------------------------------------------------------------------
  function initTextarea() {
    var input   = document.getElementById("remote-chat-input");
    var sendBtn = document.getElementById("remote-send-btn");
    if (!input) return;

    input.addEventListener("input", function () {
      input.style.height = "";
      input.style.height = Math.min(input.scrollHeight, 160) + "px";
      _syncSendEnabled();
    });

    input.addEventListener("paste", function (e) {
      var files = (e.clipboardData && e.clipboardData.files) || [];
      if (!files.length) return;
      e.preventDefault();
      addComposeFiles(files);
    });

    input.addEventListener("dragover", function (e) {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    });

    input.addEventListener("drop", function (e) {
      e.preventDefault();
      addComposeFiles((e.dataTransfer && e.dataTransfer.files) || []);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    if (sendBtn) {
      sendBtn.addEventListener("click", sendMessage);
    }
  }

  // --------------------------------------------------------------------------
  // Attach button â€” opens file picker, previews filenames as chips
  // --------------------------------------------------------------------------
  function initAttach() {
    var attachBtn   = document.getElementById("remote-attach-btn");
    var attachInput = document.getElementById("remote-attach-input");
    if (!attachBtn || !attachInput) return;

    attachBtn.addEventListener("click", function () { attachInput.click(); });

    attachInput.addEventListener("change", function () {
      addComposeFiles(attachInput.files || []);
      attachInput.value = "";
    });

    _syncSendEnabled();
  }

  window.ChatCopyUtils.bindBubbleCopyHandler(document.body);

  document.body.addEventListener("click", function (e) {

    var removeBtn = e.target.closest(".chat-attachment-chip__remove");
    if (!removeBtn) return;
    if (_composerState === "sending") return;

    var target = removeBtn.getAttribute("data-attachment-target") || "";
    if (target !== "compose") return;

    var idx = parseInt(removeBtn.getAttribute("data-attachment-index") || "-1", 10);
    if (idx < 0) return;

    var uploadedLen = composeUploaded.length;
    if (idx < uploadedLen) {
      var rec = composeUploaded[idx];
      if (!rec || !rec.id) {
        composeUploaded.splice(idx, 1);
        renderComposeAttachments();
        return;
      }
      removeBtn.disabled = true;
      deleteUploadedAttachment(rec.id).then(function () {
        composeUploaded = composeUploaded.filter(function (x) { return x.id !== rec.id; });
        renderComposeAttachments();
      }).catch(function (err) {
        removeBtn.disabled = false;
        renderStatusNote((err && err.message) || "Failed to delete attachment.");
      });
      return;
    }

    composePendingFiles.splice(idx - uploadedLen, 1);
    renderComposeAttachments();
  });

  // --------------------------------------------------------------------------
  // Eviction overlay
  // --------------------------------------------------------------------------
  function showEvictionOverlay() {
    var overlay = document.createElement("div");
    overlay.className = "remote-user-page__evict-overlay";
    overlay.innerHTML =
      '<div class="remote-user-page__evict-card">'
      + '<div class="remote-user-page__evict-icon">&#x1F6AB;</div>'
      + '<h2 class="remote-user-page__evict-title">You have been removed</h2>'
      + '<p class="remote-user-page__evict-hint">The host has removed you from this session. Close this window.</p>'
      + "</div>";
    document.body.appendChild(overlay);
  }

  // --------------------------------------------------------------------------
  // Mark user online
  // --------------------------------------------------------------------------
  function markOnline() {
    fetch("/remote/join/" + encodeURIComponent(token) + "/online/", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    }).catch(function () {});
  }

  // --------------------------------------------------------------------------
  // WebSocket lifecycle
  // --------------------------------------------------------------------------
  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = proto + "//" + location.host + "/ws/remote/chat/" + encodeURIComponent(token) + "/";
    try { _ws = new WebSocket(wsUrl); } catch (_) { return; }

    _ws.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }

      if (msg.type === "history") {
        // Inject history only when no discussions were rendered server-side.
        var container = document.getElementById("remote-chat-messages");
        if (container && container.querySelector(".remote-user-page__waiting")) {
          (msg.messages || []).forEach(function (m) {
            if (!m || !m.id || _seenMessageIds[m.id]) return;
            _seenMessageIds[m.id] = 1;
            appendBubble(m.role === "user" ? buildUserBubble(m) : buildAssistantBubble(m));
          });
        }
      } else if (msg.type === "message") {
        var m = msg.message || msg;
        if (m && m.id && _seenMessageIds[m.id]) return;
        if (m && m.id) _seenMessageIds[m.id] = 1;
        appendBubble(m.role === "user" ? buildUserBubble(m) : buildAssistantBubble(m));
      } else if (msg.type === "run_status") {
        _applyRunStatus(msg);
      } else if (msg.type === "team_choice_turn_requested") {
        var targetName = (msg.remote_user_name || "").trim();
        if (targetName && targetName === userName) {
          _setAwaitingTurn({
            round: Number(msg.round ?? 0),
            max_rounds: 0,
            chat_mode: "team",
            quorum: "team_choice",
          });
        } else {
          _setWaitingTurn();
        }
      } else if (msg.type === "team_choice_turn_submitted" || msg.type === "team_choice_turn_resolved") {
        _setWaitingTurn();
      } else if (msg.type === "quorum_progress") {
        if (msg.awaiting_host_final) {
          _setWaitingTurn();
        }
      } else if (msg.type === "quorum_committed") {
        _setWaitingTurn();
      } else if (msg.type === "evict") {
        _ws.close();
        showEvictionOverlay();
      } else if (msg.type === "error") {
        showEvictionOverlay();
      } else if (msg.type === "remote_export_enabled") {
        window._remoteExportKey = msg.export_key || "";
        _injectExportDropdowns();
      } else if (msg.type === "remote_export_disabled") {
        window._remoteExportKey = "";
        _removeExportDropdowns();
      }
    };

    _ws.onerror = function () {};
    _ws.onclose = function () { _ws = null; };
  }

  // --------------------------------------------------------------------------
  // Init
  // --------------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {
    renderLocalTimes();
    scrollToBottom();
    initTextarea();
    initAttach();
    _initComposerState();
    markOnline();
    connect();
  });
})();
