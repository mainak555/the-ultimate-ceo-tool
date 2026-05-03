"""
MCP OAuth 2.0 Authorization Code + PKCE views.

Two views, generic-page design:

  mcp_oauth_start   GET  /mcp/oauth/start/
      One entry point for both phases. Discriminated by `flow` query param:
        - flow=test : project_id + server_name (test from Project Config page)
        - flow=run  : session_id + project_id + server_name (pre-run authorize)
      Generates PKCE pair, stores state in Redis (300s TTL), redirects to
      provider auth_url.

  mcp_oauth_callback GET /mcp/oauth/callback/
      Single fixed registered redirect URI. Recovers state from Redis,
      exchanges code at token_url, increments the Redis readiness counter via
      publish_oauth_server_authorized(), branches on `flow` from state, renders
      the shared `oauth_flow.html` template for both success and error.

OAuth readiness (pre-run and mid-run gate) is delivered via WebSocket:
  ws/mcp/oauth/<session_id>/?skey=...  →  server/consumers.py::OAuthReadinessConsumer

No polling endpoint exists. The deprecated /mcp/oauth/check/<session_id>/
view has been removed.

Secret handling:
  client_secret is stored masked (SECRET_MASK) in the normalized project doc.
  It is restored from the DB on save and never returned to the browser.
  The raw client_secret is used only for the token endpoint POST and is
  never logged or set as a span attribute.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from agents.session_coordination import (
    get_and_delete_mcp_oauth_state,
    set_mcp_oauth_state,
    set_mcp_oauth_test_status,
    set_mcp_oauth_token,
)

# Default Redis TTL for a run-time Bearer token when the JWT contains no
# parseable `exp` claim.  3 h is a conservative fallback; the JWT exp is
# always preferred so the key expires exactly when the token does.
_MCP_OAUTH_DEFAULT_TTL: int = 3 * 3600
from core.tracing import set_payload_attribute, traced_block
from .services import get_project_raw, list_all_reachable_oauth_servers, verify_secret_key
from .views import _has_valid_secret  # noqa: F401  (kept for tests/external imports)

logger = logging.getLogger(__name__)


def _has_valid_oauth_secret(request) -> bool:
    """Validate the secret key for OAuth popup flows.

    Browser popups (window.open) navigate as top-level GETs and cannot set
    custom headers, so `mcp_oauth_start` receives the secret as a `skey`
    query parameter. Accept either the standard `X-App-Secret-Key` header
    (preferred when callable) or the `skey` query param.
    """
    header_key = request.headers.get("X-App-Secret-Key", "").strip()
    if header_key and verify_secret_key(header_key):
        return True
    query_key = (request.GET.get("skey") or "").strip()
    return bool(query_key) and verify_secret_key(query_key)

_PKCE_VERIFIER_BYTES: int = 64  # produces an 86-char URL-safe base64 string
_AUTO_CLOSE_SUCCESS_RUN: int = 2
_AUTO_CLOSE_SUCCESS_TEST: int = 5
_AUTO_CLOSE_ERROR: int = 30  # leave window open longer so user can read errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_callback_url(request) -> str:
    """Return the absolute OAuth callback URL to register with providers."""
    return request.build_absolute_uri(reverse("server:mcp_oauth_callback"))


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    code_verifier = secrets.token_urlsafe(_PKCE_VERIFIER_BYTES)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _resolve_oauth_config(project_raw: dict, server_name: str) -> dict | None:
    """Return the raw (unmasked) OAuth config for server_name, or None."""
    return (project_raw.get("mcp_oauth_configs") or {}).get(server_name)


def _extract_token_ttl(token_response: dict) -> int:
    """Derive token TTL (seconds) from the JWT access_token returned by the token endpoint.

    The provider response is expected to be a JSON Web Token.  We decode the
    payload (without signature verification — we trust the TLS-protected
    token endpoint) and read the ``exp`` claim, which is a UTC epoch timestamp.
    TTL = exp − now(UTC).  Because the Redis key expires at exactly this TTL,
    cache hits during an active session are always valid.

    If the JWT cannot be decoded or contains no ``exp`` claim, falls back to
    ``_MCP_OAUTH_DEFAULT_TTL`` (3 h hardcoded).  No external cap is applied.
    """
    access_token = token_response.get("access_token", "")
    if access_token and access_token.count(".") == 2:
        try:
            payload_part = access_token.split(".")[1]
            # Add padding so base64 decoding works for any payload length.
            padding = 4 - len(payload_part) % 4
            if padding != 4:
                payload_part += "=" * padding
            payload = json.loads(base64.urlsafe_b64decode(payload_part))
            exp = payload.get("exp")
            if exp:
                # exp is a UTC epoch integer; time.time() returns UTC epoch.
                remaining = int(exp) - int(time.time())
                if remaining > 60:
                    return remaining
        except Exception:  # noqa: BLE001
            pass

    return _MCP_OAUTH_DEFAULT_TTL


def _post_message_for(flow: str, server_name: str, success: bool) -> dict:
    """Build the postMessage payload the popup sends to its opener."""
    msg_type = "mcp_oauth_test_done" if flow == "test" else "mcp_oauth_done"
    return {"type": msg_type, "server_name": server_name, "success": success}


def _render_outcome(
    request,
    *,
    outcome: str,                    # "success" | "error"
    flow: str,                       # "test" | "run"
    server_name: str,
    message: str,
    auto_close_seconds: int,
) -> HttpResponse:
    """Render the shared oauth_flow.html outcome page."""
    success = outcome == "success"
    post_message = _post_message_for(flow, server_name, success)
    ctx = {
        "outcome": outcome,
        "flow": flow,
        "server_name": server_name,
        "message": message,
        "auto_close_seconds": max(0, int(auto_close_seconds)),
        "post_message_json": json.dumps(post_message),
    }
    return render(request, "server/oauth_flow.html", ctx)


def _render_error(
    request,
    *,
    flow: str,
    server_name: str,
    message: str,
) -> HttpResponse:
    """Convenience wrapper: render an error outcome with the standard auto-close."""
    return _render_outcome(
        request,
        outcome="error",
        flow=flow,
        server_name=server_name or "(unknown)",
        message=message,
        auto_close_seconds=_AUTO_CLOSE_ERROR,
    )


# ---------------------------------------------------------------------------
# View 1 — OAuth flow start (single endpoint for both flows)
# ---------------------------------------------------------------------------

@require_GET
def mcp_oauth_start(request):
    """
    GET /mcp/oauth/start/

    Single entry point for OAuth 2.0 Authorization Code + PKCE.
    Discriminated by `flow` query param:

      flow=test : project_id + server_name + skey
      flow=run  : session_id + project_id + server_name + skey

    Stores PKCE state in Redis (300s TTL), 302-redirects to provider.
    """
    flow = (request.GET.get("flow") or "").strip().lower()
    server_name = (request.GET.get("server_name") or "").strip()
    project_id = (request.GET.get("project_id") or "").strip()
    session_id = (request.GET.get("session_id") or "").strip()

    with traced_block("mcp.oauth.start", {
        "mcp.oauth.flow": flow or "(missing)",
        "mcp.oauth.server_name": server_name or "(missing)",
        "mcp.oauth.project_id": project_id or "(missing)",
        "mcp.oauth.session_id": session_id or "",
    }) as span:
        if not _has_valid_oauth_secret(request):
            logger.warning(
                "agents.mcp.oauth_start_unauthorized",
                extra={"flow": flow, "server_name": server_name, "project_id": project_id},
            )
            return _render_error(
                request, flow=flow or "test", server_name=server_name,
                message="Unauthorized: Secret Key is missing or invalid.",
            )

        if flow not in ("test", "run"):
            return _render_error(
                request, flow="test", server_name=server_name,
                message="Invalid 'flow' parameter (expected 'test' or 'run').",
            )
        if not server_name:
            return _render_error(request, flow=flow, server_name="",
                                 message="Missing 'server_name' parameter.")
        if not project_id:
            return _render_error(request, flow=flow, server_name=server_name,
                                 message="Missing 'project_id' parameter.")
        if flow == "run" and not session_id:
            return _render_error(request, flow=flow, server_name=server_name,
                                 message="Missing 'session_id' parameter for run flow.")

        project_raw = get_project_raw(project_id)
        if not project_raw:
            logger.warning(
                "agents.mcp.oauth_start_project_missing",
                extra={"project_id": project_id, "server_name": server_name},
            )
            return _render_error(request, flow=flow, server_name=server_name,
                                 message="Project not found.")

        oauth_cfg = _resolve_oauth_config(project_raw, server_name)
        if not oauth_cfg:
            logger.warning(
                "agents.mcp.oauth_start_config_missing",
                extra={"project_id": project_id, "server_name": server_name,
                       "available": list((project_raw.get("mcp_oauth_configs") or {}).keys())},
            )
            return _render_error(
                request, flow=flow, server_name=server_name,
                message=f"No OAuth config found for MCP server '{server_name}'. "
                        "Save the config first, then try again.",
            )

        auth_url = oauth_cfg.get("auth_url", "")
        client_id = oauth_cfg.get("client_id", "")
        scopes = oauth_cfg.get("scopes", "")

        if not auth_url or not client_id:
            return _render_error(
                request, flow=flow, server_name=server_name,
                message="Incomplete OAuth configuration: 'auth_url' and 'client_id' are required.",
            )

        code_verifier, code_challenge = _pkce_pair()
        state = secrets.token_urlsafe(32)
        callback_url = _build_callback_url(request)

        set_mcp_oauth_state(state, {
            "flow": flow,
            "server_name": server_name,
            "project_id": project_id,
            "session_id": session_id,
            "code_verifier": code_verifier,
            "redirect_uri": callback_url,
        })

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": callback_url,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if scopes:
            params["scope"] = scopes

        if span is not None:
            try:
                span.set_attribute("mcp.oauth.state_prefix", state[:8])
                span.set_attribute("mcp.oauth.callback_url", callback_url)
                span.set_attribute("mcp.oauth.auth_url", auth_url)
            except Exception:
                pass

        logger.info(
            "agents.mcp.oauth_start",
            extra={
                "flow": flow, "server_name": server_name, "project_id": project_id,
                "state_prefix": state[:8], "callback_url": callback_url,
            },
        )
        return HttpResponseRedirect(auth_url + "?" + urlencode(params))


# ---------------------------------------------------------------------------
# View 3 — OAuth callback (registered redirect URI)
# ---------------------------------------------------------------------------

@require_GET
def mcp_oauth_callback(request):
    """
    GET /mcp/oauth/callback/

    Provider redirect-back. Recovers PKCE state, exchanges the code for a
    token, branches on the stored `flow`, and renders oauth_flow.html.
    """
    code = (request.GET.get("code") or "").strip()
    state = (request.GET.get("state") or "").strip()
    error = (request.GET.get("error") or "").strip()
    error_description = (request.GET.get("error_description") or "").strip()

    logger.info(
        "agents.mcp.oauth_callback_received",
        extra={
            "has_code": bool(code),
            "has_state": bool(state),
            "state_prefix": state[:8] if state else "",
            "provider_error": error or "",
            "provider_error_description": error_description or "",
        },
    )

    with traced_block("mcp.oauth.callback", {
        "mcp.oauth.has_code": bool(code),
        "mcp.oauth.has_state": bool(state),
        "mcp.oauth.provider_error": error or "",
    }) as span:
        # Recover stored PKCE state first so we know the original flow + server
        state_meta = get_and_delete_mcp_oauth_state(state) if state else None
        flow = (state_meta or {}).get("flow", "test")
        server_name = (state_meta or {}).get("server_name", "")

        if span is not None:
            try:
                span.set_attribute("mcp.oauth.flow", flow)
                span.set_attribute("mcp.oauth.server_name", server_name or "(unknown)")
                span.set_attribute("mcp.oauth.state_recovered", state_meta is not None)
            except Exception:
                pass

        if error:
            logger.warning(
                "agents.mcp.oauth_callback_provider_error",
                extra={"error": error, "error_description": error_description,
                       "server_name": server_name},
            )
            return _render_error(
                request, flow=flow, server_name=server_name,
                message=f"Authorization denied by provider: {error}"
                        + (f" — {error_description}" if error_description else ""),
            )

        if not code or not state:
            return _render_error(
                request, flow=flow, server_name=server_name,
                message="Missing 'code' or 'state' query parameter.",
            )

        if not state_meta:
            logger.warning(
                "agents.mcp.oauth_callback_state_missing",
                extra={"state_prefix": state[:8]},
            )
            return _render_error(
                request, flow=flow, server_name=server_name,
                message="This authorization link has expired or already been used. "
                        "Please close this window and try again.",
            )

        project_id = state_meta.get("project_id", "")
        session_id = state_meta.get("session_id", "")
        code_verifier = state_meta.get("code_verifier", "")
        redirect_uri = state_meta.get("redirect_uri", "")

        logger.info(
            "agents.mcp.oauth_callback_state_recovered",
            extra={"flow": flow, "server_name": server_name, "project_id": project_id,
                   "session_id": session_id},
        )

        project_raw = get_project_raw(project_id)
        if not project_raw:
            return _render_error(request, flow=flow, server_name=server_name,
                                 message="Project not found. Cannot complete authorization.")

        oauth_cfg = _resolve_oauth_config(project_raw, server_name)
        if not oauth_cfg:
            return _render_error(
                request, flow=flow, server_name=server_name,
                message=f"OAuth config for '{server_name}' was removed before the flow completed.",
            )

        token_url = oauth_cfg.get("token_url", "")
        client_id = oauth_cfg.get("client_id", "")
        client_secret = oauth_cfg.get("client_secret", "")

        if not token_url:
            return _render_error(
                request, flow=flow, server_name=server_name,
                message="OAuth config has no 'token_url' — cannot exchange code.",
            )

        # ---- Token exchange ----
        with traced_block("mcp.oauth.token_exchange", {
            "mcp.oauth.token_url": token_url,
            "mcp.oauth.server_name": server_name,
            "mcp.oauth.flow": flow,
        }) as token_span:
            logger.info(
                "agents.mcp.oauth_token_exchange_start",
                extra={"server_name": server_name, "token_url": token_url, "flow": flow},
            )
            try:
                token_resp = requests.post(
                    token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code_verifier": code_verifier,
                    },
                    headers={"Accept": "application/json"},
                    timeout=15,
                )
            except requests.RequestException as exc:
                logger.exception(
                    "agents.mcp.oauth_token_exchange_network_error",
                    extra={"server_name": server_name, "project_id": project_id,
                           "token_url": token_url},
                )
                if token_span is not None:
                    try:
                        token_span.set_attribute("error.type", type(exc).__name__)
                    except Exception:
                        pass
                return _render_error(
                    request, flow=flow, server_name=server_name,
                    message=f"Network error calling token endpoint: {exc}.",
                )

            status_code = token_resp.status_code
            if token_span is not None:
                try:
                    token_span.set_attribute("http.status_code", status_code)
                except Exception:
                    pass

            try:
                token_data = token_resp.json()
            except ValueError:
                token_data = {}

            if not token_resp.ok:
                # Surface the provider's body so users can see what went wrong.
                snippet = (token_resp.text or "")[:500]
                err = token_data.get("error") or ""
                err_desc = token_data.get("error_description") or ""
                logger.warning(
                    "agents.mcp.oauth_token_exchange_http_error",
                    extra={
                        "server_name": server_name, "project_id": project_id,
                        "status_code": status_code, "provider_error": err,
                        "provider_error_description": err_desc,
                        "body_snippet": snippet,
                    },
                )
                if token_span is not None:
                    try:
                        set_payload_attribute(token_span, "output.value", snippet)
                    except Exception:
                        pass
                return _render_error(
                    request, flow=flow, server_name=server_name,
                    message=f"Token endpoint returned HTTP {status_code}"
                            + (f" — {err}" if err else "")
                            + (f": {err_desc}" if err_desc else ""),
                )

            access_token = token_data.get("access_token", "")
            if not access_token:
                error_desc = (
                    token_data.get("error_description")
                    or token_data.get("error")
                    or "no access_token in response"
                )
                logger.warning(
                    "agents.mcp.oauth_token_missing",
                    extra={"server_name": server_name, "error": error_desc,
                           "response_keys": list(token_data.keys())},
                )
                return _render_error(
                    request, flow=flow, server_name=server_name,
                    message=f"Authorization failed: {error_desc}.",
                )

            ttl = _extract_token_ttl(token_data)
            logger.info(
                "agents.mcp.oauth_token_exchange_ok",
                extra={"server_name": server_name, "flow": flow, "ttl_seconds": ttl,
                       "token_type": token_data.get("token_type", "")},
            )
            if token_span is not None:
                try:
                    token_span.set_attribute("mcp.oauth.token_ttl_seconds", ttl)
                except Exception:
                    pass

        if flow == "test":
            set_mcp_oauth_test_status(project_id, server_name)
            logger.info(
                "agents.mcp.oauth_test_authorized",
                extra={"server_name": server_name, "project_id": project_id},
            )
            return _render_outcome(
                request,
                outcome="success",
                flow="test",
                server_name=server_name,
                message=f"Authorization successful for '{server_name}'.",
                auto_close_seconds=_AUTO_CLOSE_SUCCESS_TEST,
            )

        # access_token is the Bearer token returned by token_url exchange
        # (not the authorization code). TTL = JWT exp − now(); 3 h fallback.
        set_mcp_oauth_token(session_id, server_name, access_token, ttl_seconds=ttl)
        logger.info(
            "agents.mcp.oauth_authorized",
            extra={"server_name": server_name, "session_id": session_id, "ttl_seconds": ttl},
        )

        # Publish readiness event so the WebSocket consumer can push the update
        # to the browser immediately without polling.
        from agents.session_coordination import publish_oauth_server_authorized
        total_count = len(list_all_reachable_oauth_servers(project_raw))
        publish_oauth_server_authorized(session_id, server_name, total_count)
        return _render_outcome(
            request,
            outcome="success",
            flow="run",
            server_name=server_name,
            message=f"Authorized '{server_name}' for this session.",
            auto_close_seconds=_AUTO_CLOSE_SUCCESS_RUN,
        )
