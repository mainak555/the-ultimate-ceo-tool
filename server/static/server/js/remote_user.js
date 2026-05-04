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

  var token    = window._remoteUserToken || "";
  var userName = window._remoteUserName  || "You";

  if (!token) return; // Error page â€” nothing to do.

  var _ws = null;

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
  function buildAgentBubble(msg) {
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
      + "</div>";

    el.querySelector(".chat-bubble__content").innerHTML = renderMd(content);
    return el;
  }

  /**
   * Build a user bubble matching .chat-bubble--human in chat_session_history.html.
   * @param {string} text  Raw message text.
   */
  function buildUserBubble(text) {
    var el = document.createElement("div");
    el.className = "chat-bubble chat-bubble--human";
    el.dataset.rawContent = text;
    el.innerHTML =
      '<div class="chat-bubble__meta">'
      +   '<span class="chat-bubble__name">' + escapeHtml(userName) + "</span>"
      + "</div>"
      + '<div class="chat-bubble__content"></div>';

    el.querySelector(".chat-bubble__content").innerHTML = renderMd(text);
    return el;
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
    if (!text) return;

    appendBubble(buildUserBubble(text));
    input.value = "";
    input.style.height = "";

    // Forward to backend via WebSocket so it can be processed when ready.
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      try { _ws.send(JSON.stringify({ type: "message", content: text })); } catch (_) {}
    }
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
      if (!attachList) return;
      attachList.innerHTML = "";
      if (!attachInput.files.length) { attachList.hidden = true; return; }
      attachList.hidden = false;
      Array.from(attachInput.files).forEach(function (f) {
        var chip = document.createElement("span");
        chip.className = "chat-attachment-chip";
        chip.textContent = f.name;
        attachList.appendChild(chip);
      });
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
            appendBubble(
              m.role === "user" ? buildUserBubble(m.content || "") : buildAgentBubble(m)
            );
          });
        }
      } else if (msg.type === "message") {
        var m = msg.message || msg;
        if (m.role !== "user") appendBubble(buildAgentBubble(m));
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
