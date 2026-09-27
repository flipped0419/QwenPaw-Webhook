# -*- coding: utf-8 -*-
"""Runtime registry for live WebhookChannel instances."""

from __future__ import annotations

import threading
import weakref
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .channel import WebhookChannel

_LOCK = threading.RLock()
_INSTANCES: dict[str, weakref.ReferenceType] = {}


def register_instance(agent_id: str, channel: "WebhookChannel") -> None:
    key = (agent_id or "default").strip() or "default"
    with _LOCK:
        _INSTANCES[key] = weakref.ref(channel)


def unregister_instance(agent_id: str, channel: "WebhookChannel") -> None:
    key = (agent_id or "default").strip() or "default"
    with _LOCK:
        ref = _INSTANCES.get(key)
        if ref is None:
            return
        current = ref()
        if current is None or current is channel:
            _INSTANCES.pop(key, None)


def _live_instances() -> dict[str, "WebhookChannel"]:
    live: dict[str, "WebhookChannel"] = {}
    dead: list[str] = []
    with _LOCK:
        for key, ref in _INSTANCES.items():
            obj = ref()
            if obj is None:
                dead.append(key)
            else:
                live[key] = obj
        for key in dead:
            _INSTANCES.pop(key, None)
    return live


def select_instance(agent_id: str = "") -> tuple[str, Optional["WebhookChannel"]]:
    live = _live_instances()
    requested = (agent_id or "").strip()
    if requested:
        return requested, live.get(requested)
    if len(live) == 1:
        key, value = next(iter(live.items()))
        return key, value
    return "", None


def instance_status() -> dict:
    live = _live_instances()
    return {
        "count": len(live),
        "agents": sorted(live.keys()),
    }
