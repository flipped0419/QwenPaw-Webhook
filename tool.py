# -*- coding: utf-8 -*-
"""Agent tool: send_webhook."""

from __future__ import annotations

from typing import Any, Dict, Optional

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolChunk
from qwenpaw.plugins import get_tool_config

from .sender import send_webhook_request


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _chunk(state: ToolResultState, text: str) -> ToolChunk:
    return ToolChunk(
        state=state,
        content=[TextBlock(type="text", text=text)],
    )


async def send_webhook(
    target: str,
    content: str,
    title: str = "",
    data: Optional[Dict[str, Any]] = None,
) -> ToolChunk:
    """Send a message to a preconfigured webhook target.

    Use this tool only when an external notification or webhook delivery is
    actually required. `target` should normally be a trusted alias configured
    by the administrator, not a URL.

    Args:
        target: Trusted webhook target alias, e.g. ``notify``.
        content: Main notification/body text.
        title: Optional title.
        data: Optional structured JSON data for target templates.

    Returns:
        ToolChunk describing whether delivery succeeded.
    """
    cfg = get_tool_config("send_webhook") or {}
    targets_file = str(cfg.get("targets_file", "") or "")
    allow_raw_url = _as_bool(cfg.get("allow_raw_url", False))
    try:
        timeout = float(cfg.get("timeout", 15) or 15)
    except (TypeError, ValueError):
        timeout = 15.0

    try:
        result = await send_webhook_request(
            target,
            title=title,
            content=content,
            data=data or {},
            targets_file=targets_file,
            allow_raw_url=allow_raw_url,
            default_timeout=timeout,
        )
    except Exception as exc:  # tool boundary: return error to agent
        return _chunk(ToolResultState.ERROR, f"Webhook delivery failed: {exc}")

    return _chunk(
        ToolResultState.SUCCESS,
        f"Webhook delivered to '{result.target}' (HTTP {result.status_code}).",
    )
