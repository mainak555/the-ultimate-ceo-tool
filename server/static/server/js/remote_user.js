/**
 * remote_user.js — Remote-user standalone chat page
 *
 * Responsibilities:
 * 1. On load: POST /remote/join/<token>/online/ to mark user as online.
 * 2. Open WebSocket to ws/remote/chat/<token>/ for live messages.
 * 3. Render history markdown in existing bubbles.
 * 4. Append new agent messages as they arrive.
 * 5. Show an eviction overlay when ignored by the host.
 */
(function () {
  "use strict";

  var token = window._remoteUserToken || "";
  var sessionId = window._remoteSessionId || "";

  if (!token) return; // Error page — no token, nothing to do.

  // ------------------------------------------------------------------
  // Markdown rendering (reuse marked.js loaded by the page)
  // ------------------------------------------------------------------
  function renderMarkdown(text) {
    if (window.marked && typeof window.marked.parse === "function") {
      try { return window.marked.parse(text || ""); } catch (_) {}
    }
    return (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderAllMarkdown() {
    var bodies = document.querySelectorAll(".chat-bubble__markdown");
    bodies.forEach(function (el) {
      var raw = (el.closest(".chat-bubble") || el).dataset.rawContent || "";
      if (raw && !el.dataset.rendered) {
        el.innerHTML = renderMarkdown(raw);
        el.dataset.rendered = "1";
      }
    });
  }

  // ------------------------------------------------------------------
  // Local time rendering
  // ------------------------------------------------------------------
  function renderLocalTimes() {
    document.querySelectorAll(".local-time[data-utc]").forEach(function (el) {
      if (el.dataset.rendered) return;
      var d = new Date(el.dataset.utc);
      if (!isNaN(d.getTime())) {
        el.textContent = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
        el.dataset.rendered = "1";
      }
    });
  }

  // ------------------------------------------------------------------
  // Append a new agent message bubble
  // ------------------------------------------------------------------
  function appendMessage(msg) {
    var container = document.getElementById("remote-chat-messages");
    if (!container) return;

    var waiting = container.querySelector(".remote-user-page__waiting");
    if (waiting) waiting.remove();

    var ts = msg.timestamp || "";
    var timeHtml = ts ? '<time class="local-time" data-utc="' + escapeHtml(ts) + '"></time>' : "";
    var agentName = msg.agent_name || "";
    var content = msg.content || "";

    var html = '<div class="chat-message chat-message--agent">'
      + '<div class="chat-bubble chat-bubble--agent" data-raw-content="' + escapeHtml(content) + '">'
      + '<div class="chat-bubble__meta">'
      + '<span class="chat-bubble__name">' + escapeHtml(agentName) + '</span>'
      + timeHtml
      + '</div>'
      + '<div class="chat-bubble__body chat-bubble__markdown markdown-body"></div>'
      + '</div>'
      + '</div>';
    container.insertAdjacentHTML("beforeend", html);
    renderAllMarkdown();
    renderLocalTimes();
    container.scrollTop = container.scrollHeight;
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ------------------------------------------------------------------
  // Eviction overlay
  // ------------------------------------------------------------------
  function showEvictionOverlay() {
    var overlay = document.createElement("div");
    overlay.className = "remote-user-page__evict-overlay";
    overlay.innerHTML = '<div class="remote-user-page__evict-card">'
      + '<div class="remote-user-page__evict-icon">&#x1F6AB;</div>'
      + '<h2 class="remote-user-page__evict-title">You have been removed</h2>'
      + '<p class="remote-user-page__evict-hint">The host has removed you from this session. Close this window.</p>'
      + '</div>';
    document.body.appendChild(overlay);
  }

  // ------------------------------------------------------------------
  // Mark user online
  // ------------------------------------------------------------------
  function markOnline() {
    fetch("/remote/join/" + encodeURIComponent(token) + "/online/", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
    }).catch(function () { /* non-fatal */ });
  }

  // ------------------------------------------------------------------
  // WebSocket lifecycle
  // ------------------------------------------------------------------
  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = proto + "//" + location.host + "/ws/remote/chat/" + encodeURIComponent(token) + "/";
    var ws;
    try { ws = new WebSocket(wsUrl); } catch (_) { return; }

    ws.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }

      if (msg.type === "history") {
        // History already rendered server-side; skip re-render if populated.
        // If the container is empty, inject history messages.
        var container = document.getElementById("remote-chat-messages");
        if (container && container.querySelector(".remote-user-page__waiting")) {
          (msg.messages || []).forEach(function (m) {
            if (m.role !== "user") appendMessage(m);
          });
        }
      } else if (msg.type === "message") {
        var m = msg.message || msg;
        if (m.role !== "user") appendMessage(m);
      } else if (msg.type === "evict") {
        ws.close();
        showEvictionOverlay();
      } else if (msg.type === "error") {
        showEvictionOverlay();
      }
    };

    ws.onerror = function () { /* silent — page stays usable with static history */ };
    ws.onclose = function () { /* no reconnect — remote user page is read-only */ };
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {
    renderAllMarkdown();
    renderLocalTimes();

    var container = document.getElementById("remote-chat-messages");
    if (container) container.scrollTop = container.scrollHeight;

    if (token) {
      markOnline();
      connect();
    }
  });
})();
