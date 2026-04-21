/**
 * provider_registry.js - Provider-agnostic registry for integration features.
 *
 * Scope:
 *   - Register provider capabilities (export modal + config sync)
 *   - Lookup and invoke provider features without hardcoded provider switches
 */

(function () {
  "use strict";

  var _providers = {};

  function _normalizeName(name) {
    return String(name || "").trim().toLowerCase();
  }

  function register(name, capabilities) {
    var key = _normalizeName(name);
    if (!key || !capabilities || typeof capabilities !== "object") return;

    var existing = _providers[key] || {};
    _providers[key] = Object.assign({}, existing, capabilities);
  }

  function get(name) {
    var key = _normalizeName(name);
    return key ? (_providers[key] || null) : null;
  }

  function has(name) {
    return !!get(name);
  }

  function openExportModal(name, context) {
    var provider = get(name);
    if (!provider || typeof provider.openExportModal !== "function") return false;
    provider.openExportModal(context || {});
    return true;
  }

  function syncConfigState(name, context) {
    var provider = get(name);
    if (!provider || typeof provider.syncConfigState !== "function") return false;
    provider.syncConfigState(context || {});
    return true;
  }

  window.ProviderRegistry = {
    register: register,
    get: get,
    has: has,
    openExportModal: openExportModal,
    syncConfigState: syncConfigState,
  };
})();
