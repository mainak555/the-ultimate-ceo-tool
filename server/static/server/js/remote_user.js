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
    el.innerHTML =
      '<div class="chat-bubble__avatar">' + escapeHtml(initial) + "</div>"
      + '<div class="chat-bubble__body">'
      +   '<div class="chat-bubble__meta">'
      +     '<span class="chat-bubble__name">' + escapeHtml(agentName) + "</span>"
      +     timeHtml
      +   "</div>"
      +   '<div class="chat-bubble__content"></div>'
      +   renderMessageAttachments(msg.attachments || [])
      + "</div>";

    el.querySelector(".chat-bubble__content").innerHTML = renderMd(content);
    return el;
  }

  /**
   * Build a user bubble matching .chat-bubble--human in chat_session_history.html.
   * @param {string} text  Raw message text.
   */
  function buildUserBubble(msg) {
    var displayName = msg.agent_name || userName;
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
    var container = document.getElementById("remote-chat-messages");
    if (!container) return;
    var waiting = container.querySelector(".remote-user-page__waiting");
    if (waiting) waiting.remove();
    container.appendChild(el);
    renderLocalTimes();
    scrollToBottom();
  }

  // --------------------------------------------------------------------------
  // Send message
  // --------------------------------------------------------------------------
  function sendMessage() {
    var input = document.getElementById("remote-chat-input");
    if (!input) return;
    var text = input.value.trim();
    var hasAttachments = composePendingFiles.length > 0 || composeUploaded.length > 0;
    if (!text && !hasAttachments) return;

    var sendBtn = document.getElementById("remote-send-btn");
    if (sendBtn) sendBtn.disabled = true;

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
          if (!r.ok && r.status !== 202) throw new Error(data.error || data.message || "Failed to send response");
          if (data.status === "locked") throw new Error(data.message || "Another participant already continued this run.");
          input.value = "";
          input.style.height = "";
          clearComposeAttachments();
          renderStatusNote(data.status === "waiting_host"
            ? "All participant inputs received. Waiting for host to continue."
            : "Response submitted.");
          return data;
        });
      })
      .catch(function (err) {
        renderStatusNote(err.message || "Failed to send response.");
      })
      .finally(function () {
        if (sendBtn) sendBtn.disabled = false;
      });
  }

  function renderStatusNote(text) {
    if (!text) return;
    var c = document.getElementById("remote-chat-messages");
    if (!c) return;
    var el = document.createElement("div");
    el.className = "chat-status-badge chat-status-badge--remote-users";
    el.textContent = text;
    c.appendChild(el);
    scrollToBottom();
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

  function renderComposeAttachments() {
    var attachList = document.getElementById("remote-compose-attachments");
    if (!attachList) return;
    var all = composeUploaded.concat(composePendingFiles);
    if (!all.length) {
      attachList.innerHTML = "";
      attachList.hidden = true;
      return;
    }
    attachList.hidden = false;
    attachList.innerHTML = all.map(function (att) {
      return '<span class="chat-attachment-chip">' + escapeHtml(att.filename || "file") + "</span>";
    }).join("");
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
    var attachList  = document.getElementById("remote-compose-attachments");
    if (!attachBtn || !attachInput) return;

    attachBtn.addEventListener("click", function () { attachInput.click(); });

    attachInput.addEventListener("change", function () {
      Array.from(attachInput.files || []).forEach(function (file) {
        composePendingFiles.push(toUploadRecord(file));
      });
      attachInput.value = "";
      renderComposeAttachments();
    });
  }

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
      } else if (msg.type === "quorum_progress") {
        if (msg.awaiting_host_final) {
          renderStatusNote("All participant inputs received. Waiting for host to continue.");
        }
      } else if (msg.type === "quorum_committed") {
        renderStatusNote("Run resumed by " + (msg.winner || "host") + ".");
      } else if (msg.type === "evict") {
        _ws.close();
        showEvictionOverlay();
      } else if (msg.type === "error") {
        showEvictionOverlay();
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
    markOnline();
    connect();
  });
})();
