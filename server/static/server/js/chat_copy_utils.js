/**
 * chat_copy_utils.js - Shared chat-bubble copy helpers for Home/Remote/Guest.
 */
(function () {
  "use strict";

  var COPY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  var CHECK_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';

  function buildCopyBtnHtml() {
    return '<button type="button" class="chat-bubble__copy-btn" title="Copy message" aria-label="Copy message">' + COPY_ICON + "</button>";
  }

  function getCopyTextFromBubble(bubbleEl) {
    var md = bubbleEl && bubbleEl.dataset ? (bubbleEl.dataset.rawContent || "") : "";
    var attachmentLinks = bubbleEl ? bubbleEl.querySelectorAll(".chat-message-attachment") : [];
    if (attachmentLinks.length) {
      md += "\n\n**Attachments:**\n";
      attachmentLinks.forEach(function (anchor) {
        var href = (anchor.getAttribute("href") || "").trim();
        var nameSpan = anchor.querySelector(".chat-message-attachment__name");
        var filenameRaw = (nameSpan ? nameSpan.textContent : anchor.textContent || "").trim();
        if (!filenameRaw) return;

        var filenameEscaped = filenameRaw.replace(/\[/g, "\\[").replace(/\]/g, "\\]");
        if (!href) {
          md += "- " + filenameRaw + "\n";
          return;
        }

        var absoluteUrl;
        try {
          absoluteUrl = new URL(href, window.location.origin).href;
        } catch (e) {
          absoluteUrl = href;
        }
        md += "- [" + filenameEscaped + "](" + absoluteUrl + ")\n";
      });
    }
    return md.trim();
  }

  function showCopiedFeedback(btn) {
    if (!btn) return;
    btn.innerHTML = CHECK_ICON;
    btn.classList.add("chat-bubble__copy-btn--copied");
    btn.title = "Copied!";
    setTimeout(function () {
      btn.innerHTML = COPY_ICON;
      btn.classList.remove("chat-bubble__copy-btn--copied");
      btn.title = "Copy message";
    }, 2000);
  }

  function fallbackCopyText(text, btn) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand("copy"); showCopiedFeedback(btn); } catch (e) { /* silent */ }
    document.body.removeChild(ta);
  }

  function copyTextToClipboard(text, btn) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () {
        showCopiedFeedback(btn);
      }).catch(function () {
        fallbackCopyText(text, btn || document.createElement("button"));
      });
    }
    fallbackCopyText(text, btn || document.createElement("button"));
    return Promise.resolve();
  }

  function bindBubbleCopyHandler(delegateRoot) {
    var root = delegateRoot || document.body;
    root.addEventListener("click", function (e) {
      var btn = e.target.closest(".chat-bubble__copy-btn");
      if (!btn) return;
      var bubble = btn.closest(".chat-bubble");
      if (!bubble) return;
      var text = getCopyTextFromBubble(bubble);
      copyTextToClipboard(text, btn);
    });
  }

  window.ChatCopyUtils = {
    buildCopyBtnHtml: buildCopyBtnHtml,
    getCopyTextFromBubble: getCopyTextFromBubble,
    copyTextToClipboard: copyTextToClipboard,
    bindBubbleCopyHandler: bindBubbleCopyHandler,
  };
})();
