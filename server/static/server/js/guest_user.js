/**
 * guest_user.js - Standalone readonly guest page behavior.
 */
(function () {
  "use strict";

  var token = window._guestToken || "";
  if (!token) return;

  var ws = null;
  var seenMessageIds = Object.create(null);

  function appendMessage(msg) {
    var box = window.ChatSurfaceUtils.getOrCreateHistoryContainer("guest-chat-messages", "guest-chat-history-msgs");
    if (!box) return;
    var waiting = box.querySelector(".guest-user-page__waiting");
    if (waiting) waiting.remove();
    var ts = msg.timestamp || new Date().toISOString();
    var role = (msg.role || "").toLowerCase();
    var contentHtml = window.MarkdownViewer.render(msg.content || "");
    var attachmentsHtml = window.ChatSurfaceUtils.renderMessageAttachments(msg.attachments || [], {
      fallbackIcon: true,
      fallbackImage: true,
    });

    var html;
    if (role === "user") {
      html = '<div class="chat-bubble chat-bubble--human" data-raw-content="' + window.MarkdownViewer.escapeHtml(msg.content || "") + '">'
        + '<div class="chat-bubble__meta">'
        + '<span class="chat-bubble__name">' + window.MarkdownViewer.escapeHtml(msg.agent_name || "User") + '</span>'
        + '<span class="chat-bubble__time"><time class="local-time" data-utc="' + ts + '">' + ts + '</time></span>'
        + window.ChatCopyUtils.buildCopyBtnHtml()
        + '</div>'
        + '<div class="chat-bubble__content">' + contentHtml + '</div>'
        + attachmentsHtml
        + '</div>';
    } else {
      var name = msg.agent_name || "Agent";
      var avatar = window.MarkdownViewer.escapeHtml(name.slice(0, 1).toUpperCase());
      html = '<div class="chat-bubble chat-bubble--ai" data-raw-content="' + window.MarkdownViewer.escapeHtml(msg.content || "") + '">'
        + '<div class="chat-bubble__avatar">' + avatar + '</div>'
        + '<div class="chat-bubble__body">'
        + '<div class="chat-bubble__meta">'
        + '<span class="chat-bubble__name">' + window.MarkdownViewer.escapeHtml(name) + '</span>'
        + '<span class="chat-bubble__time"><time class="local-time" data-utc="' + ts + '">' + ts + '</time></span>'
        + window.ChatCopyUtils.buildCopyBtnHtml()
        + '</div>'
        + '<div class="chat-bubble__content">' + contentHtml + '</div>'
        + attachmentsHtml
        + '</div>'
        + '</div>';
    }

    box.insertAdjacentHTML("beforeend", html);
    window.MermaidViewer.hydrate(box.lastElementChild || box);
    window.ChatSurfaceUtils.renderLocalTimes();
    window.ChatSurfaceUtils.scrollToBottom("#guest-chat-messages");
  }

  function setAgentsWorkingBadge(show) {
    var c = document.getElementById("guest-chat-messages");
    if (!c) return;
    var existing = c.querySelector(".chat-status-badge--running");
    if (!show) {
      if (existing) existing.remove();
      return;
    }
    if (existing) return;
    c.insertAdjacentHTML("beforeend", '<div class="chat-status-badge chat-status-badge--running">\u2699 Agents at work</div>');
    window.ChatSurfaceUtils.scrollToBottom("#guest-chat-messages");
  }

  function appendTerminalStatusBadge(type) {
    var c = document.getElementById("guest-chat-messages");
    if (!c) return;
    c.querySelectorAll(".chat-status-badge--stopped, .chat-status-badge--completed").forEach(function (el) {
      el.remove();
    });
    setAgentsWorkingBadge(false);
    var label = type === "completed" ? "Run completed" : "Run stopped";
    c.insertAdjacentHTML("beforeend", '<div class="chat-status-badge chat-status-badge--' + type + '">' + label + "</div>");
    window.ChatSurfaceUtils.scrollToBottom("#guest-chat-messages");
  }

  function showEvictedOverlay() {
    if (document.querySelector(".guest-user-page__evict-overlay")) return;
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div class="guest-user-page__evict-overlay">'
      + '<div class="guest-user-page__evict-card">'
      + '<div class="guest-user-page__evict-icon">&#x26A0;</div>'
      + '<div class="guest-user-page__evict-title">Access revoked</div>'
      + '<p class="guest-user-page__evict-hint">This guest link is no longer active.</p>'
      + '</div>'
      + '</div>'
    );
  }

  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = proto + "//" + location.host + "/ws/guest/chat/" + encodeURIComponent(token) + "/";

    try {
      ws = new WebSocket(wsUrl);
    } catch (_) {
      return;
    }

    ws.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }

      if (msg.type === "history") {
        var hasServerHistory = document.querySelector("#guest-chat-history-msgs .chat-bubble") !== null
          || document.querySelector("#guest-chat-messages .chat-bubble") !== null;
        if (hasServerHistory) return;
        (msg.messages || []).forEach(function (m) {
          if (m && m.id) seenMessageIds[m.id] = 1;
          appendMessage(m || {});
        });
        return;
      }

      if (msg.type === "message") {
        var rec = msg.message || {};
        if (rec.id && seenMessageIds[rec.id]) return;
        if (rec.id) seenMessageIds[rec.id] = 1;
        appendMessage(rec);
        return;
      }

      if (msg.type === "run_status") {
        var status = msg.status || "";
        if (status === "running") {
          setAgentsWorkingBadge(true);
        } else if (status === "completed") {
          appendTerminalStatusBadge("completed");
        } else if (status === "stopped") {
          appendTerminalStatusBadge("stopped");
        } else if (status === "idle") {
          setAgentsWorkingBadge(false);
        }
        return;
      }

      if (msg.type === "evict") {
        showEvictedOverlay();
        try { ws.close(); } catch (_) {}
      }
    };

    ws.onclose = function () {
      ws = null;
    };
  }

  window.ChatCopyUtils.bindBubbleCopyHandler(document.body);

  document.addEventListener("DOMContentLoaded", function () {
    window.MermaidViewer.hydrate(document.getElementById("guest-chat-messages"));
    window.ChatSurfaceUtils.renderLocalTimes();
    window.ChatSurfaceUtils.scrollToBottom("#guest-chat-messages");
    connect();
  });
})();
