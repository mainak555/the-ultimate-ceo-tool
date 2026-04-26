/**
 * app.js - Shared cross-page behavior only.
 *
 * Scope:
 *   - Secret key helpers exposed via window.AppCommon
 *   - HTMX secret header injection
 *   - Shared toast auto-dismiss
 *   - UTC → browser-local time rendering for time[data-utc] elements
 */

/**
 * Convert all <time data-utc="ISO"> elements to browser-local display time.
 * Exposed as window.renderLocalTimes so other modules (home.js) can call it
 * after dynamically inserting new bubbles.
 *
 * Graceful fallback: if the stored value is not a valid ISO string (e.g. old
 * bare "HH:MM" strings already in MongoDB), the raw value is shown unchanged.
 */
function renderLocalTimes() {
  document.querySelectorAll("time[data-utc]").forEach(function (el) {
    var iso = el.dataset.utc;
    if (!iso) return;
    var d = new Date(iso);
    if (isNaN(d.getTime())) {
      // Old bare "HH:MM" record or otherwise unparseable — display as-is.
      el.textContent = iso;
      return;
    }
    // Full ISO strings contain "T"; bare time strings do not.
    var hasDate = iso.indexOf("T") !== -1;
    var opts = hasDate
      ? { dateStyle: "medium", timeStyle: "short" }
      : { timeStyle: "short" };
    el.textContent = d.toLocaleString(navigator.language, opts);
  });
}

window.renderLocalTimes = renderLocalTimes;

document.addEventListener("DOMContentLoaded", function () {
  function getSecretKeyInput() {
    return document.getElementById("global-secret-key");
  }

  function getSecretKey() {
    var input = getSecretKeyInput();
    return input ? input.value.trim() : "";
  }

  function hasSecretKey() {
    return !!getSecretKey();
  }

  window.AppCommon = {
    getSecretKeyInput: getSecretKeyInput,
    getSecretKey: getSecretKey,
    hasSecretKey: hasSecretKey,
  };

  document.body.addEventListener("htmx:configRequest", function (e) {
    var secretKey = getSecretKey();
    if (secretKey) {
      e.detail.headers["X-App-Secret-Key"] = secretKey;
    }
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    renderLocalTimes();

    var toast = document.getElementById("toast");
    if (!toast) return;

    setTimeout(function () {
      toast.style.transition = "opacity 0.3s";
      toast.style.opacity = "0";
      setTimeout(function () { toast.remove(); }, 300);
    }, 4000);
  });

  renderLocalTimes();
});
