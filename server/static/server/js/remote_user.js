(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var sessionIdEl = document.getElementById("remote-session-id");
    var tokenEl = document.getElementById("remote-user-token");
    var projectIdEl = document.getElementById("remote-project-id");
    var capabilityEl = document.getElementById("remote-export-capability");
    var heartbeatEl = document.getElementById("remote-heartbeat-seconds");
    var csrfEl = document.getElementById("csrf-token-value");

    var chatMessages = document.getElementById("chat-messages");
    var chatInput = document.getElementById("chat-input");
    var sendBtn = document.getElementById("chat-send-btn");
    var attachBtn = document.getElementById("chat-attach-btn");
    var attachInput = document.getElementById("chat-attachment-input");
    var composeAttachments = document.getElementById("chat-compose-attachments");
    var presenceStrip = document.getElementById("remote-presence-strip");

    if (!sessionIdEl || !tokenEl || !chatMessages || !chatInput || !sendBtn) return;

    var sessionId = (sessionIdEl.value || "").trim();
    var remoteToken = (tokenEl.value || "").trim();
    var projectId = (projectIdEl && projectIdEl.value || "").trim();
    var exportCapability = (capabilityEl && capabilityEl.value || "").trim();
    var csrfToken = (csrfEl && csrfEl.value || "").trim();
    var heartbeatSeconds = parseInt((heartbeatEl && heartbeatEl.value) || "30", 10);
    if (!heartbeatSeconds || heartbeatSeconds < 5) heartbeatSeconds = 30;

    var pendingFiles = [];
    var uploaded = [];
    var heartbeatTimer = null;
    var ws = null;
    var reconnectTimer = null;
    var reconnectDelayMs = 1000;
    var wsPendingSubmit = null;

    function esc(text) {
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

    function fileIcon(filename) {
      var ext = (filename || "").split(".").pop().toLowerCase();
      var known = {
        pdf: 1, doc: 1, docx: 1, xls: 1, xlsx: 1, ppt: 1, pptx: 1,
        csv: 1, txt: 1, json: 1, xml: 1, md: 1,
      };
      var name = known[ext] ? ext : "document";
      return "/static/server/assets/icons/file-" + name + ".svg";
    }

    function chipHtml(att, idx) {
      var name = esc(att.filename || "file");
      var iconCls = att.is_image
        ? "chat-attachment-chip__thumb"
        : "chat-attachment-chip__thumb chat-attachment-chip__thumb--icon";
      var thumb = att.thumbnail_url
        ? '<img class="' + iconCls + '" src="' + att.thumbnail_url + '" alt="' + name + '">'
        : "";
      return '<div class="chat-attachment-chip">'
        + thumb
        + '<span class="chat-attachment-chip__name">' + name + '</span>'
        + '<span class="chat-attachment-chip__meta">' + formatBytes(att.size_bytes) + '</span>'
        + '<button class="chat-attachment-chip__remove" type="button" data-attachment-index="' + idx + '">&#x00D7;</button>'
        + '</div>';
    }

    function renderComposeAttachments() {
      var all = uploaded.concat(pendingFiles);
      if (!all.length) {
        composeAttachments.hidden = true;
        composeAttachments.innerHTML = "";
        return;
      }
      var html = "";
      all.forEach(function (att, idx) { html += chipHtml(att, idx); });
      composeAttachments.innerHTML = html;
      composeAttachments.hidden = false;
    }

    function addFiles(fileList) {
      var files = Array.prototype.slice.call(fileList || []);
      if (!files.length) return;
      var cap = 10 - uploaded.length - pendingFiles.length;
      if (cap <= 0) return;
      files.slice(0, cap).forEach(function (file) {
        var isImg = /^image\//i.test(file.type || "");
        pendingFiles.push({
          id: "",
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          size_bytes: file.size || 0,
          is_image: isImg,
          thumbnail_url: isImg ? "" : fileIcon(file.name),
          content_url: "",
          _file: file,
        });
      });
      renderComposeAttachments();
    }

    function getHeaders(includeJson) {
      var headers = {
        "X-Remote-User-Token": remoteToken,
        "X-CSRFToken": csrfToken,
      };
      if (includeJson) headers["Content-Type"] = "application/json";
      return headers;
    }

    function uploadPendingAttachments() {
      if (!pendingFiles.length) {
        return Promise.resolve(uploaded.map(function (x) { return x.id; }));
      }
      var form = new FormData();
      var batch = pendingFiles.slice();
      pendingFiles = [];
      batch.forEach(function (row) {
        if (row && row._file) form.append("files", row._file);
      });
      return fetch("/chat/sessions/" + sessionId + "/remote/attachments/", {
        method: "POST",
        headers: getHeaders(false),
        body: form,
      }).then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.error || "Upload failed");
          return d;
        });
      }).then(function (d) {
        uploaded = uploaded.concat(d.attachments || []);
        renderComposeAttachments();
        return uploaded.map(function (x) { return x.id; });
      }).catch(function (err) {
        pendingFiles = batch.concat(pendingFiles);
        renderComposeAttachments();
        throw err;
      });
    }

    function applyState(d) {
      if (typeof d.history_html === "string") {
        var isFirstRender = chatMessages.getAttribute("data-has-rendered") !== "1";
        var wasNearBottom = (chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight) <= 60;
        chatMessages.innerHTML = d.history_html;
        if (window.renderLocalTimes) window.renderLocalTimes();
        chatMessages.setAttribute("data-has-rendered", "1");
        if (isFirstRender || wasNearBottom) {
          chatMessages.scrollTop = chatMessages.scrollHeight;
        }
      }
      if (presenceStrip && Array.isArray(d.participants)) {
        presenceStrip.innerHTML = d.participants.map(function (p) {
          var cls = "remote-presence-chip";
          cls += p.online ? " remote-presence-chip--online" : " remote-presence-chip--offline";
          if (p.active) cls += " remote-presence-chip--active";
          return '<span class="' + cls + '">' + esc(p.name || "User") + '</span>';
        }).join("");
      }
      var canSend = !!d.can_send;
      chatInput.disabled = !canSend;
      sendBtn.disabled = !canSend;
      if (attachBtn) attachBtn.disabled = !canSend;
      if (attachInput) attachInput.disabled = !canSend;
      chatInput.placeholder = canSend ? "Send your response" : "Waiting for your turn";
    }

    function sendHeartbeat() {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "heartbeat" }));
      }
      fetch("/chat/sessions/" + sessionId + "/remote/heartbeat/", {
        method: "POST",
        headers: getHeaders(false),
      }).catch(function () {});
    }

    function wsUrl() {
      var proto = window.location.protocol === "https:" ? "wss://" : "ws://";
      return proto + window.location.host + "/ws/chat/"
        + encodeURIComponent(sessionId) + "/remote-user/"
        + encodeURIComponent(remoteToken) + "/";
    }

    function requestStateSync() {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "sync_state" }));
      }
    }

    function scheduleReconnect() {
      if (reconnectTimer) return;
      reconnectTimer = setTimeout(function () {
        reconnectTimer = null;
        connectWs();
      }, reconnectDelayMs);
      reconnectDelayMs = Math.min(reconnectDelayMs * 2, 15000);
    }

    function connectWs() {
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
      try {
        ws = new WebSocket(wsUrl());
      } catch (_err) {
        scheduleReconnect();
        return;
      }

      ws.addEventListener("open", function () {
        reconnectDelayMs = 1000;
        requestStateSync();
      });

      ws.addEventListener("message", function (evt) {
        var data;
        try {
          data = JSON.parse(evt.data || "{}");
        } catch (_err) {
          return;
        }
        if (!data || typeof data !== "object") return;
        if (data.type === "state") {
          applyState(data);
          return;
        }
        if (data.type === "ack") {
          if (wsPendingSubmit) {
            wsPendingSubmit.resolve(data);
            wsPendingSubmit = null;
          }
          return;
        }
        if (data.type === "error") {
          if (wsPendingSubmit) {
            wsPendingSubmit.reject(new Error(data.error || "Unable to send"));
            wsPendingSubmit = null;
          }
          return;
        }
      });

      ws.addEventListener("close", function () {
        if (wsPendingSubmit) {
          wsPendingSubmit.reject(new Error("Connection closed while sending."));
          wsPendingSubmit = null;
        }
        scheduleReconnect();
      });

      ws.addEventListener("error", function () {
        if (wsPendingSubmit) {
          wsPendingSubmit.reject(new Error("Connection error while sending."));
          wsPendingSubmit = null;
        }
        scheduleReconnect();
      });
    }

    function submitReplyWs(text, attachmentIds) {
      return new Promise(function (resolve, reject) {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          reject(new Error("Connection is not ready yet."));
          return;
        }
        if (wsPendingSubmit) {
          reject(new Error("A message is already being sent."));
          return;
        }
        wsPendingSubmit = { resolve: resolve, reject: reject };
        try {
          ws.send(JSON.stringify({
            type: "submit_reply",
            text: text,
            attachment_ids: attachmentIds || [],
          }));
        } catch (_err) {
          wsPendingSubmit = null;
          reject(new Error("Unable to send message."));
        }
      });
    }

    function submitRemoteMessage() {
      var text = (chatInput.value || "").trim();
      var hasAttachments = pendingFiles.length > 0 || uploaded.length > 0;
      if (!text && !hasAttachments) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        alert("Connection is not ready yet. Please wait a moment and try again.");
        return;
      }
      sendBtn.disabled = true;
      uploadPendingAttachments().then(function (attachmentIds) {
        return submitReplyWs(text, attachmentIds || []);
      }).then(function () {
        chatInput.value = "";
        chatInput.style.height = "auto";
        uploaded = [];
        pendingFiles = [];
        renderComposeAttachments();
      }).catch(function (err) {
        alert(err.message || "Unable to send response");
      }).finally(function () {
        if (!chatInput.disabled) sendBtn.disabled = false;
      });
    }

    chatInput.addEventListener("input", function () {
      chatInput.style.height = "auto";
      chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
    });

    chatInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!sendBtn.disabled) submitRemoteMessage();
      }
    });

    sendBtn.addEventListener("click", function () {
      if (!sendBtn.disabled) submitRemoteMessage();
    });

    if (attachBtn && attachInput) {
      attachBtn.addEventListener("click", function () {
        if (!attachBtn.disabled) attachInput.click();
      });
      attachInput.addEventListener("change", function () {
        addFiles(attachInput.files);
        attachInput.value = "";
      });
    }

    chatInput.addEventListener("paste", function (e) {
      var files = (e.clipboardData && e.clipboardData.files) || [];
      if (files.length) {
        e.preventDefault();
        addFiles(files);
      }
    });

    chatInput.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });

    chatInput.addEventListener("drop", function (e) {
      e.preventDefault();
      addFiles((e.dataTransfer && e.dataTransfer.files) || []);
    });

    document.body.addEventListener("click", function (e) {
      var removeBtn = e.target.closest(".chat-attachment-chip__remove");
      if (removeBtn) {
        var idx = parseInt(removeBtn.getAttribute("data-attachment-index") || "-1", 10);
        if (idx >= 0) {
          var uploadedLen = uploaded.length;
          if (idx < uploadedLen) uploaded.splice(idx, 1);
          else pendingFiles.splice(idx - uploadedLen, 1);
          renderComposeAttachments();
        }
        return;
      }

      var copyBtn = e.target.closest(".chat-bubble__copy-btn");
      if (copyBtn) {
        var bubble = copyBtn.closest(".chat-bubble");
        if (!bubble) return;
        var text = bubble.dataset.rawContent || "";
        var names = bubble.querySelectorAll(".chat-message-attachment__name");
        if (names.length) {
          text += "\n\n**Attachments:**\n";
          names.forEach(function (el) {
            text += "- " + el.textContent.trim() + "\n";
          });
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text.trim()).catch(function () {});
        }
        return;
      }

      var toggle = e.target.closest(".export-dropdown__toggle");
      if (toggle) {
        var dropdown = toggle.closest("[data-export-dropdown]");
        if (!dropdown) return;
        document.querySelectorAll("[data-export-dropdown]").forEach(function (el) {
          var menu = el.querySelector(".export-dropdown__menu");
          var btn = el.querySelector(".export-dropdown__toggle");
          if (menu) menu.hidden = true;
          if (btn) btn.setAttribute("aria-expanded", "false");
        });
        var menu2 = dropdown.querySelector(".export-dropdown__menu");
        if (menu2) {
          menu2.hidden = false;
          toggle.setAttribute("aria-expanded", "true");
        }
        return;
      }

      var item = e.target.closest(".export-dropdown__item");
      if (item) {
        var provider = item.dataset.provider || "";
        var discussionId = (item.dataset.discussionId || "").trim();
        if (!provider || !discussionId || !window.ProviderRegistry) return;
        window.ProviderRegistry.openExportModal(provider, {
          provider: provider,
          sessionId: sessionId,
          discussionId: discussionId,
          secretKey: "",
          csrfToken: csrfToken,
          projectId: projectId,
          authHeaderName: "X-Remote-Export-Capability",
          authHeaderValue: exportCapability,
        });
      }
    });

    connectWs();
    heartbeatTimer = setInterval(sendHeartbeat, heartbeatSeconds * 1000);
    sendHeartbeat();
    requestStateSync();

    window.addEventListener("beforeunload", function () {
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        ws.close();
      }
    });
  });
})();
