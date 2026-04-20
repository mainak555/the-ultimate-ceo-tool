"""LLM-based extraction of structured items from discussion text.

Uses the first available model from the project's agent list to run the
extraction prompt and parse the JSON output.
"""

from __future__ import annotations

import json
import re

from agents.factory import build_model_client


def run_extraction(
    system_prompt: str,
    discussion_text: str,
    project: dict,
    model: str | None = None,
    temperature: float = 0.0,
) -> list[dict]:
    """Run the extraction agent synchronously and return the parsed items list.

    Parameters
    ----------
    system_prompt : str
        The extraction system prompt (from export_mapping config).
    discussion_text : str
        Concatenated discussion to extract from.
    project : dict
        Raw project document (with agent list for model selection fallback).
    model : str | None
        Explicit model name from export_mapping config. Falls back to the
        first assistant agent's model when blank.
    temperature : float
        Sampling temperature for the extraction call (default ``0.0``).

    Returns
    -------
    list[dict]
        Parsed ``items`` array from the extractor JSON output.
    """
    if not system_prompt:
        raise ValueError("Extraction system_prompt is empty. Configure it in the project integrations.")

    # Resolve model — use explicit mapping model or fall back to first agent
    model_name = (model or "").strip()
    if not model_name:
        agents = project.get("agents") or []
        if not agents:
            raise ValueError("No agents configured in project — cannot determine extraction model.")
        agent_cfg = agents[0]
        model_name = agent_cfg.get("model") or agent_cfg.get("model_name")
        if not model_name:
            raise ValueError("First agent has no model configured.")

    client = build_model_client(model_name)

    # Build messages for a simple completion call
    from autogen_core.models import SystemMessage, UserMessage

    messages = [
        SystemMessage(content=system_prompt),
        UserMessage(content=discussion_text, source="user"),
    ]

    import asyncio

    async def _run():
        result = await client.create(messages)
        return result.content

    raw = asyncio.run(_run())

    # Parse JSON from the response — handle markdown code fences
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Extractor returned invalid JSON: {exc}\n\nRaw output:\n{text}")

    # Normalize common model output variants.
    # Expected shape is {"items": [...]}, but some models may return
    # {"items": null} (no extraction) or omit the key entirely.
    if isinstance(parsed, dict):
        items = parsed.get("items")
        if items is None:
            return []
    else:
        items = parsed

    if not isinstance(items, list):
        raise ValueError(f"Expected 'items' array in extractor output, got: {type(items).__name__}")

    return items
