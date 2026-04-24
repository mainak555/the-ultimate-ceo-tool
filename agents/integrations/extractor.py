"""LLM-based extraction of structured items from discussion text.

Uses the first available model from the project's agent list to run the
extraction prompt and parse the JSON output.
"""

from __future__ import annotations

import json
import logging
import re
import time

from agents.factory import build_model_client
from agents.tracing import traced_block

logger = logging.getLogger(__name__)


_MARKDOWN_PATTERN = re.compile(
    r"(^\s{0,3}#{1,6}\s+)|(^\s{0,3}[-*+]\s+)|(^\s{0,3}>\s+)|(```)|(`[^`]+`)|(\[[^\]]+\]\([^\)]+\))",
    re.MULTILINE,
)


def _infer_text_mime_type(text: str) -> str:
    payload = (text or "").strip()
    if not payload:
        return "text/plain"
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, (dict, list, tuple)):
            return "application/json"
    except Exception:
        pass
    if _MARKDOWN_PATTERN.search(payload):
        return "text/markdown"
    return "text/plain"


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
    input_payload = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": discussion_text},
    ]

    import asyncio

    async def _run():
        result = await client.create(messages)
        return result.content

    logger.info(
        "agents.extraction.started",
        extra={"model_name": model_name, "discussion_chars": len(discussion_text or "")},
    )
    with traced_block(
        "agents.extraction.run",
        {
            "gen_ai.system": "autogen",
            "gen_ai.operation.name": "text_completion",
            "app.component": "agents.integrations.extractor",
            "app.model_name": model_name,
            "app.discussion_chars": len(discussion_text or ""),
            "input.value": json.dumps(input_payload, ensure_ascii=False),
            "input.mime_type": "application/json",
        },
    ) as span:
        t0 = time.monotonic()
        raw = asyncio.run(_run())
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if span is not None:
            span.set_attribute("output.value", raw)
            span.set_attribute("output.mime_type", _infer_text_mime_type(raw))

        # Parse JSON from the response — handle markdown code fences.
        text = raw.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.exception(
                "agents.extraction.parse_failed",
                extra={
                    "model_name": model_name,
                    "elapsed_ms": elapsed_ms,
                    "raw_snippet": text[:500],
                },
            )
            raise ValueError(f"Extractor returned invalid JSON: {exc}\n\nRaw output:\n{text}")

        # Normalize common model output variants.
        # Expected shape is {"items": [...]}, but some models may return
        # {"items": null} (no extraction) or omit the key entirely.
        if isinstance(parsed, dict):
            items = parsed.get("items")
            if items is None:
                if span is not None:
                    span.set_attribute("app.item_count", 0)
                    span.set_attribute("app.elapsed_ms", elapsed_ms)
                logger.info(
                    "agents.extraction.completed",
                    extra={"model_name": model_name, "elapsed_ms": elapsed_ms, "item_count": 0},
                )
                return []
        else:
            items = parsed

        if not isinstance(items, list):
            logger.error(
                "agents.extraction.shape_mismatch",
                extra={"model_name": model_name, "actual_type": type(items).__name__},
            )
            raise ValueError(f"Expected 'items' array in extractor output, got: {type(items).__name__}")

        if span is not None:
            span.set_attribute("app.item_count", len(items))
            span.set_attribute("app.elapsed_ms", elapsed_ms)
        logger.info(
            "agents.extraction.completed",
            extra={"model_name": model_name, "elapsed_ms": elapsed_ms, "item_count": len(items)},
        )
        return items
