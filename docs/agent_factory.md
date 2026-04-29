# Agent Factory — Model Client Reference

The agent factory (`agents/factory.py`) creates provider-specific [AutoGen model clients](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/tutorial/models.html) from model names defined in `agent_models.json`.

## `agent_models.json` Schema

Each key is a **model name** (the identifier shown in the UI and used at runtime).

```jsonc
{
  "model_name": {
    "provider": "openai | anthropic | google | azure_openai | azure_anthropic",
    "model": "<actual-model-id>",       // optional; model identifier for AutoGen (default: key name)
    "endpoint": "<url>",                // optional for all; falls back to <PROVIDER_UPPER>_API_URL env var
    "api_version": "<version>",         // optional; azure_openai only (default: 2024-12-01-preview)
    "deployment_name": "<deployment>",  // optional; azure_* only (default: key name)
    "model_info": { ... }               // optional; per-model capability overrides (see below)
  }
}
```

### Field Reference

| Field             | Type   | Required | Providers          | Description |
|-------------------|--------|----------|--------------------|-------------|
| `provider`        | string | **yes**  | all                | Provider identifier — must match a registered builder |
| `model`           | string | no       | all                | Actual model identifier passed as `model=` to the AutoGen client. Defaults to the JSON key. Use when the provider resolves to a versioned name (e.g. `gpt-5.4-mini-2026-03-17`) |
| `endpoint`        | string | no       | all       | API endpoint URL. Resolution order: JSON `endpoint` -> `{PROVIDER_UPPER}_API_URL`. Azure providers still require a resolved endpoint after fallback; non-Azure providers keep it optional. |
| `api_version`     | string | no       | `azure_openai`     | Azure API version string. Defaults to `2024-12-01-preview` |
| `deployment_name` | string | no       | `azure_openai`, `azure_anthropic` | Azure deployment name override. Defaults to the key name |
| `model_info`      | object | no       | all                | Per-model capability overrides — merged over defaults (see Model Info) |

### Example

```json
{
  "gpt-4o": {
    "provider": "openai"
  },
  "gpt-4.1": {
    "provider": "azure_openai",
    "endpoint": "https://myresource.cognitiveservices.azure.com/",
    "api_version": "2024-12-01-preview"
  },
  "gpt-5.4-mini": {
    "provider": "azure_openai",
    "endpoint": "https://myresource.cognitiveservices.azure.com/",
    "api_version": "2024-12-01-preview",
    "model": "gpt-5.4-mini-2026-03-17",
    "deployment_name": "gpt-54-mini-deployment"
  },
  "claude-3-7-sonnet": {
    "provider": "anthropic"
  },
  "claude-sonnet-4-6": {
    "provider": "azure_anthropic",
    "endpoint": "https://myresource.services.ai.azure.com/anthropic/"
  },
  "gemini-2.5-flash": {
    "provider": "google"
  }
}
```

---

## Environment Variables

API keys follow the convention `{PROVIDER_UPPER}_API_KEY`.
Endpoint fallbacks follow the convention `{PROVIDER_UPPER}_API_URL`.

| Provider          | API Key Env Var            | Endpoint Env Var          |
|-------------------|----------------------------|---------------------------|
| `openai`          | `OPENAI_API_KEY`           | `OPENAI_API_URL`          |
| `anthropic`       | `ANTHROPIC_API_KEY`        | `ANTHROPIC_API_URL`       |
| `google`          | `GOOGLE_API_KEY`           | `GOOGLE_API_URL`          |
| `azure_openai`    | `AZURE_OPENAI_API_KEY`     | `AZURE_OPENAI_API_URL`    |
| `azure_anthropic` | `AZURE_ANTHROPIC_API_KEY`  | `AZURE_ANTHROPIC_API_URL` |

Set these in your `.env` file or shell environment. The factory raises `ValueError` at runtime if a required API key is missing, or if an Azure endpoint cannot be resolved from either JSON `endpoint` or the provider URL env var.

---

## Provider Registry

Each provider maps to a builder function, an AutoGen client class, and specific constructor arguments.

| Provider | Builder | AutoGen Client | Import Path |
|----------|---------|---------------|-------------|
| `openai` | `_build_openai` | `OpenAIChatCompletionClient` | `autogen_ext.models.openai` |
| `anthropic` | `_build_anthropic` | `AnthropicChatCompletionClient` | `autogen_ext.models.anthropic` |
| `google` | `_build_google` | `OpenAIChatCompletionClient` | `autogen_ext.models.openai` |
| `azure_openai` | `_build_azure_openai` | `AzureOpenAIChatCompletionClient` | `autogen_ext.models.openai` |
| `azure_anthropic` | `_build_azure_anthropic` | `AnthropicChatCompletionClient` | `autogen_ext.models.anthropic` |

### Provider Details

#### `openai` — Direct OpenAI API

```python
from autogen_ext.models.openai import OpenAIChatCompletionClient

client = OpenAIChatCompletionClient(
    model="gpt-4o",
    api_key=OPENAI_API_KEY,
    # base_url=endpoint,      # optional — from JSON "endpoint" or OPENAI_API_URL fallback
)
```

- `model_info` is **not** injected by default (AutoGen auto-detects for known OpenAI models). Pass `model_info` in `agent_models.json` only if using an unrecognized model name.

#### `anthropic` — Direct Anthropic API

```python
from autogen_ext.models.anthropic import AnthropicChatCompletionClient

client = AnthropicChatCompletionClient(
    model="claude-3-7-sonnet",
    api_key=ANTHROPIC_API_KEY,
    # base_url=endpoint,      # optional — from JSON "endpoint" or ANTHROPIC_API_URL fallback
)
```

- Same `model_info` behavior as `openai` — injected only when `model_info` is present in the JSON entry.

#### `google` — Google Gemini (OpenAI-compatible)

Gemini exposes an [OpenAI-compatible API](https://ai.google.dev/gemini-api/docs/openai), so the factory uses `OpenAIChatCompletionClient` instead of a Gemini-specific client.

```python
from autogen_ext.models.openai import OpenAIChatCompletionClient

client = OpenAIChatCompletionClient(
    model="gemini-2.5-flash",
    api_key=GOOGLE_API_KEY,
    model_info={
        "json_output": False,
        "function_calling": False,
        "vision": False,
        "family": "unknown",
        "structured_output": False,
    },
    # base_url=endpoint,      # optional — from JSON "endpoint" or GOOGLE_API_URL fallback
)
```

- `model_info` is **always** injected (defaults merged with any per-model overrides) because AutoGen cannot auto-detect Gemini capabilities via the OpenAI-compatible adapter.

#### `azure_openai` — Azure AI Foundry (OpenAI)

```python
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient

client = AzureOpenAIChatCompletionClient(
    model=model,                    # from "model" or key name (for token/cost estimation)
    azure_endpoint=endpoint,        # from JSON "endpoint" or AZURE_OPENAI_API_URL (required after fallback)
    azure_deployment=deployment_name,  # from "deployment_name" or key name (for Azure routing)
    api_version="2024-12-01-preview",
    api_key=AZURE_OPENAI_API_KEY,
    model_info={
        "json_output": False,
        "function_calling": False,
        "vision": False,
        "family": "unknown",
        "structured_output": False,
    },
)
```

- `endpoint` resolution order: JSON `endpoint` first, then `AZURE_OPENAI_API_URL` env var.
- Azure OpenAI still requires a resolved endpoint after fallback.
- `model` — the actual model identifier for AutoGen token/cost estimation. Defaults to the key name. Set when Azure resolves to a versioned name (e.g. `"model": "gpt-5.4-mini-2026-03-17"`).
- `deployment_name` defaults to the key name if omitted.
- `api_version` defaults to `2024-12-01-preview` if omitted.

#### `azure_anthropic` — Azure AI Foundry (Anthropic)

```python
from autogen_ext.models.anthropic import AnthropicChatCompletionClient

client = AnthropicChatCompletionClient(
    model=model,                    # from "model" or key name
    base_url=endpoint,              # from JSON "endpoint" or AZURE_ANTHROPIC_API_URL (required after fallback)
    api_key=AZURE_ANTHROPIC_API_KEY,
    model_info={
        "json_output": False,
        "function_calling": False,
        "vision": False,
        "family": "unknown",
        "structured_output": False,
    },
)
```

- `endpoint` resolution order: JSON `endpoint` first, then `AZURE_ANTHROPIC_API_URL` env var.
- Azure Anthropic still requires a resolved endpoint after fallback.
- `model` — the actual model identifier. Defaults to the key name.
- `deployment_name` defaults to the key name if omitted.

---

## Model Info

`model_info` tells AutoGen what capabilities a model supports. It is required for providers where AutoGen cannot auto-detect capabilities (Azure, Google/Gemini via OpenAI adapter).

### Default Values

The factory applies these defaults for every provider that requires `model_info`:

```json
{
  "json_output": false,
  "function_calling": false,
  "vision": false,
  "family": "unknown",
  "structured_output": false
}
```

### Per-Model Overrides

To override specific capabilities for a model, add a `model_info` object to its entry in `agent_models.json`. Only the keys you specify are overridden; the rest keep their defaults.

```json
{
  "gpt-4.1": {
    "provider": "azure_openai",
    "endpoint": "https://myresource.cognitiveservices.azure.com/",
    "model_info": {
      "function_calling": true,
      "json_output": true,
      "structured_output": true
    }
  }
}
```

This merges to:

```json
{
  "json_output": true,
  "function_calling": true,
  "vision": false,
  "family": "unknown",
  "structured_output": true
}
```

### When to Override

| Capability          | Set `true` when…                               |
|---------------------|-------------------------------------------------|
| `function_calling`  | Model supports tool/function calling             |
| `json_output`       | Model can produce structured JSON responses      |
| `structured_output` | Model supports a structured output schema        |
| `vision`            | Model accepts image inputs                       |
| `family`            | Set to `"gpt-4o"`, `"claude"`, etc. if known     |

### `function_calling` is required for MCP tools

Whenever an assistant agent has `mcp_tools` set to `shared` or `dedicated`,
the team builder attaches one or more `McpWorkbench` instances to the agent.
At call time AutoGen forwards those tools to the model client, and the
underlying OpenAI/Anthropic/Azure clients raise:

```text
ValueError: Model does not support function calling
```

…unless the resolved `model_info.function_calling` is `True`. The factory
default is `False`, so for any provider that requires `model_info` (Azure
OpenAI, Azure Anthropic, Google Gemini), the catalog entry **must** declare
`"function_calling": true` for that model to be usable with MCP tools.

For `openai` and `anthropic` direct providers, `model_info` is only injected
when present in the JSON entry — AutoGen falls back to its internal table for
known model names (e.g. `gpt-4o`, `claude-3-7-sonnet`). Custom or
unrecognized model identifiers must declare `model_info` explicitly to opt
into function calling.

If a model legitimately does not support function calling (some reasoning,
audio, or embedding models), the correct configuration is to keep
`mcp_tools = "none"` on every agent that uses it — do not set
`function_calling: true` on a model that cannot honor it.

---

## Adding a New Provider

1. **Define a builder** in `agents/factory.py`:

   ```python
   def _build_my_provider(model_name: str, metadata: dict, **kwargs: Any):
       cls = _import_class("autogen_ext.models.xxx", "XxxChatCompletionClient")
       kwargs.setdefault("api_key", _require_env("MY_PROVIDER_API_KEY"))
       kwargs.setdefault("model_info", _resolve_model_info(metadata))
       return cls(model=model_name, **kwargs)
   ```

2. **Register it** in `_PROVIDER_BUILDERS`:

   ```python
   _PROVIDER_BUILDERS["my_provider"] = _build_my_provider
   ```

3. **Add a model entry** in `agent_models.json`:

   ```json
   {
     "my-model": {
       "provider": "my_provider",
       "endpoint": "https://..."
     }
   }
   ```

4. **Set the env var**: `MY_PROVIDER_API_KEY=sk-...`

5. **Install the extra** if needed: update `requirements.txt` with the appropriate `autogen-ext` extra.

---

## Dependencies

```
autogen-ext[openai,azure,anthropic]>=0.4
```

This installs provider extras for:
- `openai` — `OpenAIChatCompletionClient`, `AzureOpenAIChatCompletionClient`
- `azure` — Azure identity and credential support
- `anthropic` — `AnthropicChatCompletionClient`

Google Gemini uses the OpenAI-compatible adapter, so no separate `google` extra is needed.

---

## Public API

```python
from agents.factory import build_model_client

# Returns a fully configured AutoGen model client
client = build_model_client("gpt-4.1", temperature=0.7)
```

`build_model_client(model_name, **kwargs)` is the single entry point. It:
1. Loads model metadata from `agent_models.json` via `config_loader.get_model_metadata()`
2. Resolves the provider from the `provider` field
3. Dispatches to the corresponding builder function
4. Injects API keys from environment variables
5. Returns a ready-to-use AutoGen `ChatCompletionClient`

Any extra `**kwargs` (e.g. `temperature`) are forwarded to the underlying client constructor.
