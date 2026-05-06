"""Factory for creating AutoGen model clients from model names.

Provider registry
-----------------
Each provider maps to a dedicated builder function that knows the correct
AutoGen client class and constructor signature for that backend:

  openai           — direct OpenAI API             (OpenAIChatCompletionClient)
  anthropic        — direct Anthropic API           (AnthropicChatCompletionClient)
  google           — Google Gemini (OpenAI-compat)  (OpenAIChatCompletionClient)
  azure_openai     — Azure AI Foundry OpenAI        (AzureOpenAIChatCompletionClient)
  azure_anthropic  — Azure AI Foundry Anthropic     (AnthropicChatCompletionClient + base_url)

To add a new provider, define a _build_<name> function and add one line to
_PROVIDER_BUILDERS at the bottom.

See docs/agent_factory.md for full schema reference and environment setup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from importlib import import_module
from typing import Any

from .config_loader import get_model_metadata

logger = logging.getLogger(__name__)

# Retry configuration — tunable via environment variables.
_ANTHROPIC_MAX_RETRIES = int(os.getenv("ANTHROPIC_MAX_RETRIES", "3"))
_ANTHROPIC_RETRY_BASE_DELAY = float(os.getenv("ANTHROPIC_RETRY_BASE_DELAY", "5.0"))


# ---------------------------------------------------------------------------
# Anthropic 529 retry proxy
# ---------------------------------------------------------------------------

def _is_overloaded_error(exc: BaseException) -> bool:
    """Return True for Anthropic HTTP 529 OverloadedError (checked by name to avoid
    a hard import from the anthropic package at module load time)."""
    return type(exc).__name__ == "OverloadedError" or getattr(exc, "status_code", None) == 529


class _RetryAnthropicClient:
    """Transparent proxy around AnthropicChatCompletionClient that retries
    ``create()`` / ``create_stream()`` calls when Anthropic returns HTTP 529
    (overloaded) using exponential back-off with jitter.

    All other attribute accesses are delegated directly to the inner client so
    the object is a drop-in replacement for the AutoGen chat-completion protocol.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    # ------------------------------------------------------------------
    # Retry-wrapped async entry points
    # ------------------------------------------------------------------

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        max_retries = _ANTHROPIC_MAX_RETRIES
        base_delay = _ANTHROPIC_RETRY_BASE_DELAY
        last_exc: BaseException | None = None
        for attempt in range(max_retries):
            try:
                return await self._inner.create(*args, **kwargs)
            except Exception as exc:
                if not _is_overloaded_error(exc):
                    raise
                last_exc = exc
                if attempt >= max_retries - 1:
                    break
                delay = base_delay * (2 ** attempt) + random.uniform(0.0, 1.0)
                logger.warning(
                    "agents.model_client.anthropic_overloaded_retry",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "delay_seconds": round(delay, 1),
                    },
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def create_stream(self, *args: Any, **kwargs: Any) -> Any:
        max_retries = _ANTHROPIC_MAX_RETRIES
        base_delay = _ANTHROPIC_RETRY_BASE_DELAY
        last_exc: BaseException | None = None
        for attempt in range(max_retries):
            try:
                async for chunk in self._inner.create_stream(*args, **kwargs):
                    yield chunk
                return
            except Exception as exc:
                if not _is_overloaded_error(exc):
                    raise
                last_exc = exc
                if attempt >= max_retries - 1:
                    break
                delay = base_delay * (2 ** attempt) + random.uniform(0.0, 1.0)
                logger.warning(
                    "agents.model_client.anthropic_overloaded_retry_stream",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "delay_seconds": round(delay, 1),
                    },
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Transparent delegation for all other protocol attributes
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _import_class(module_name: str, class_name: str):
    """Dynamically import a class; raise RuntimeError if the package is missing."""
    try:
        module = import_module(module_name)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"AutoGen client '{class_name}' from '{module_name}' is not available. "
            "Ensure the required autogen-ext provider extra is installed."
        ) from exc


def _require_env(env_name: str) -> str:
    """Return env var value or raise ValueError naming the missing variable."""
    value = os.getenv(env_name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable '{env_name}' is not set.")
    return value


def _resolve_endpoint(model_name: str, metadata: dict, provider: str, *, required: bool) -> str:
    """Resolve endpoint from metadata, then provider env var, then requirement policy.

    Resolution order:
      1) ``agent_models.json`` entry field ``endpoint``
      2) ``{PROVIDER_UPPER}_API_URL`` environment variable
      3) if required, raise ``ValueError``
    """
    endpoint = str(metadata.get("endpoint") or "").strip()
    if endpoint:
        return endpoint

    env_name = f"{provider.upper()}_API_URL"
    endpoint = str(os.getenv(env_name, "") or "").strip()
    if endpoint:
        return endpoint

    if required:
        raise ValueError(
            f"Model '{model_name}' is missing required endpoint. "
            f"Set 'endpoint' in agent_models.json or set environment variable '{env_name}'."
        )
    return ""


def _default_model_info() -> dict:
    """Return conservative default model_info for providers that require it."""
    return {
        "json_output": False,
        "function_calling": False,
        "vision": False,
        "family": "unknown",
        "structured_output": False,
    }


def _resolve_model_info(metadata: dict) -> dict:
    """Merge per-model model_info overrides from metadata over defaults.

    If the model entry in agent_models.json contains a ``model_info`` dict,
    its keys override the corresponding defaults.  Unknown keys are ignored.
    """
    info = _default_model_info()
    overrides = metadata.get("model_info")
    if isinstance(overrides, dict):
        for key in info:
            if key in overrides:
                info[key] = overrides[key]
    return info


def _resolve_model_name(model_key: str, metadata: dict) -> str:
    """Return the model identifier to pass as ``model=`` to the AutoGen client.

    If the entry in agent_models.json contains a non-empty ``model`` field it
    is used (e.g. a versioned name like ``gpt-5.4-mini-2026-03-17``).  Otherwise
    the catalog key itself is returned.
    """
    return str(metadata.get("model") or model_key).strip()


# ---------------------------------------------------------------------------
# Per-provider builder functions
# ---------------------------------------------------------------------------

def _build_openai(model_name: str, metadata: dict, **kwargs: Any):
    """Direct OpenAI API via OpenAIChatCompletionClient.

    agent_models.json fields:
      endpoint   (optional) — custom base URL (e.g. proxy or compatible endpoint)
    Env var: OPENAI_API_KEY
    """
    cls = _import_class("autogen_ext.models.openai", "OpenAIChatCompletionClient")
    kwargs.setdefault("api_key", _require_env("OPENAI_API_KEY"))
    endpoint = _resolve_endpoint(model_name, metadata, "openai", required=False)
    if endpoint:
        kwargs.setdefault("base_url", endpoint)
    model_info = metadata.get("model_info")
    if isinstance(model_info, dict):
        kwargs.setdefault("model_info", _resolve_model_info(metadata))
    return cls(model=_resolve_model_name(model_name, metadata), **kwargs)


def _build_anthropic(model_name: str, metadata: dict, **kwargs: Any):
    """Direct Anthropic API via AnthropicChatCompletionClient.

    agent_models.json fields:
      endpoint   (optional) — custom base URL (e.g. proxy or compatible endpoint)
    Env var: ANTHROPIC_API_KEY
    """
    cls = _import_class("autogen_ext.models.anthropic", "AnthropicChatCompletionClient")
    kwargs.setdefault("api_key", _require_env("ANTHROPIC_API_KEY"))
    endpoint = _resolve_endpoint(model_name, metadata, "anthropic", required=False)
    if endpoint:
        kwargs.setdefault("base_url", endpoint)
    model_info = metadata.get("model_info")
    if isinstance(model_info, dict):
        kwargs.setdefault("model_info", _resolve_model_info(metadata))
    return _RetryAnthropicClient(cls(model=_resolve_model_name(model_name, metadata), **kwargs))


def _build_google(model_name: str, metadata: dict, **kwargs: Any):
    """Google Gemini via OpenAI-compatible endpoint (OpenAIChatCompletionClient).

    Gemini models expose an OpenAI-compatible API, so we reuse
    OpenAIChatCompletionClient and pass model_info so AutoGen
    knows the model's capabilities.

    agent_models.json fields:
      endpoint   (optional) — custom base URL override
    Env var: GOOGLE_API_KEY
    """
    cls = _import_class("autogen_ext.models.openai", "OpenAIChatCompletionClient")
    kwargs.setdefault("api_key", _require_env("GOOGLE_API_KEY"))
    endpoint = _resolve_endpoint(model_name, metadata, "google", required=False)
    if endpoint:
        kwargs.setdefault("base_url", endpoint)
    kwargs.setdefault("model_info", _resolve_model_info(metadata))
    return cls(model=_resolve_model_name(model_name, metadata), **kwargs)


def _build_azure_openai(model_name: str, metadata: dict, **kwargs: Any):
    """Azure AI Foundry OpenAI deployment via AzureOpenAIChatCompletionClient.

    agent_models.json fields:
      endpoint        (required) — Azure resource endpoint URL
                                   e.g. https://<resource>.cognitiveservices.azure.com/
      api_version     (optional) — defaults to 2024-12-01-preview
      deployment_name (optional) — deployment name override; defaults to the model key
    Env var: AZURE_OPENAI_API_KEY
    """
    cls = _import_class("autogen_ext.models.openai", "AzureOpenAIChatCompletionClient")
    endpoint = _resolve_endpoint(model_name, metadata, "azure_openai", required=True)
    api_version = str(metadata.get("api_version") or "2024-12-01-preview").strip()
    deployment = str(metadata.get("deployment_name") or model_name).strip()
    kwargs.setdefault("api_key", _require_env("AZURE_OPENAI_API_KEY"))
    kwargs.setdefault("azure_endpoint", endpoint)
    kwargs.setdefault("azure_deployment", deployment)
    kwargs.setdefault("api_version", api_version)
    kwargs.setdefault("model_info", _resolve_model_info(metadata))
    return cls(model=_resolve_model_name(model_name, metadata), **kwargs)


def _build_azure_anthropic(model_name: str, metadata: dict, **kwargs: Any):
    """Anthropic model on Azure AI Foundry via AnthropicChatCompletionClient.

    Passes base_url so the underlying Anthropic SDK routes requests to the
    Azure AI Services endpoint instead of api.anthropic.com.

    agent_models.json fields:
      endpoint        (required) — Azure AI Services Anthropic endpoint URL
                                   e.g. https://<resource>.services.ai.azure.com/anthropic/
      deployment_name (optional) — deployment name override; defaults to the model key
    Env var: AZURE_ANTHROPIC_API_KEY
    """
    cls = _import_class("autogen_ext.models.anthropic", "AnthropicChatCompletionClient")
    endpoint = _resolve_endpoint(model_name, metadata, "azure_anthropic", required=True)
    deployment = str(metadata.get("deployment_name") or model_name).strip()
    kwargs.setdefault("api_key", _require_env("AZURE_ANTHROPIC_API_KEY"))
    kwargs.setdefault("base_url", endpoint)
    kwargs.setdefault("model_info", _resolve_model_info(metadata))
    return _RetryAnthropicClient(cls(model=_resolve_model_name(model_name, metadata), **kwargs))


# ---------------------------------------------------------------------------
# Provider registry — add new providers here
# ---------------------------------------------------------------------------

_PROVIDER_BUILDERS: dict[str, Any] = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "google": _build_google,
    "azure_openai": _build_azure_openai,
    "azure_anthropic": _build_azure_anthropic,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_model_client(model_name: str, **kwargs: Any):
    """Build a provider-specific AutoGen model client from a catalog model name.

    Resolves the model's provider from agent_models.json, injects credentials
    from environment variables, and constructs the appropriate AutoGen client.
    """
    metadata = get_model_metadata(model_name)
    provider = str(metadata.get("provider") or "").strip().lower()
    if not provider:
        raise ValueError(f"Model '{model_name}' is missing 'provider' in agent_models.json.")

    builder = _PROVIDER_BUILDERS.get(provider)
    if builder is None:
        supported = ", ".join(_PROVIDER_BUILDERS)
        raise ValueError(
            f"Unsupported provider '{provider}' for model '{model_name}'. "
            f"Supported providers: {supported}."
        )

    try:
        client = builder(model_name, metadata, **kwargs)
    except Exception:
        logger.exception(
            "agents.model_client.failed",
            extra={"provider": provider, "model_name": model_name},
        )
        raise
    logger.info(
        "agents.model_client.created",
        extra={"provider": provider, "model_name": model_name},
    )
    return client
