# -*- coding: utf-8 -*-
"""QwenPaw Webhook Channel."""

from __future__ import annotations

import hmac
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from qwenpaw.app.channels.base import BaseChannel, OnReplySent, ProcessHandler
from qwenpaw.schemas import ContentType, TextContent
from qwenpaw.app.channels.renderer import ChannelDisplayConfig

from .runtime import register_instance, unregister_instance
from .sender import load_targets, send_webhook_request

logger = logging.getLogger(__name__)
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _cfg(config: Any, name: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_component(value: str, fallback: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("_", (value or "").strip()).strip("._-")
    return cleaned[:96] or fallback


class WebhookChannel(BaseChannel):
    """Generic inbound/outbound webhook channel.

    Outbound messages are POSTed to trusted aliases in webhook_targets.json.
    Inbound messages are accepted by the plugin HTTP router and queued into
    the agent through this channel instance.
    """

    channel = "webhook"
    uses_manager_queue = True
    streaming_enabled = False

    def __init__(
        self,
        process: ProcessHandler,
        *,
        enabled: bool = True,
        default_target: str = "",
        targets_file: str = "",
        allow_raw_url: bool = False,
        inbound_enabled: bool = False,
        inbound_secret: str = "",
        inbound_sources: str = "",
        suppress_no_reply: bool = True,
        timeout: float = 15.0,
        max_inbound_bytes: int = 262_144,
        workspace_dir: Optional[Path] = None,
        on_reply_sent: OnReplySent = None,
        display_config: ChannelDisplayConfig | None = None,
        no_text_debounce: bool = True,
    ) -> None:
        super().__init__(
            process,
            on_reply_sent=on_reply_sent,
            display_config=display_config,
            no_text_debounce=no_text_debounce,
            streaming_enabled=False,
        )
        self.enabled = enabled
        self.default_target = (default_target or "").strip()
        self.targets_file = (targets_file or "").strip()
        self.allow_raw_url = bool(allow_raw_url)
        self.inbound_enabled = bool(inbound_enabled)
        self.inbound_secret = (inbound_secret or "").strip()
        self.inbound_sources = {
            item.strip()
            for item in (inbound_sources or "").split(",")
            if item.strip()
        }
        self.suppress_no_reply = bool(suppress_no_reply)
        try:
            self.timeout = max(1.0, min(float(timeout), 120.0))
        except (TypeError, ValueError):
            self.timeout = 15.0
        try:
            self.max_inbound_bytes = max(1024, min(int(max_inbound_bytes), 2_097_152))
        except (TypeError, ValueError):
            self.max_inbound_bytes = 262_144
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self.agent_id = self.workspace_dir.name if self.workspace_dir else "default"
        self._started = False

    @classmethod
    def from_config(
        cls,
        process: ProcessHandler,
        config: Any,
        on_reply_sent: OnReplySent = None,
        display_config: ChannelDisplayConfig | None = None,
        no_text_debounce: bool = True,
        workspace_dir: Optional[Path] = None,
    ) -> "WebhookChannel":
        return cls(
            process,
            enabled=_as_bool(_cfg(config, "enabled", False)),
            default_target=str(_cfg(config, "default_target", "") or ""),
            targets_file=str(_cfg(config, "targets_file", "") or ""),
            allow_raw_url=_as_bool(_cfg(config, "allow_raw_url", False)),
            inbound_enabled=_as_bool(_cfg(config, "inbound_enabled", False)),
            inbound_secret=str(_cfg(config, "inbound_secret", "") or ""),
            inbound_sources=str(_cfg(config, "inbound_sources", "") or ""),
            suppress_no_reply=_as_bool(_cfg(config, "suppress_no_reply", True), True),
            timeout=_cfg(config, "timeout", 15),
            max_inbound_bytes=_cfg(config, "max_inbound_bytes", 262144),
            workspace_dir=workspace_dir,
            on_reply_sent=on_reply_sent,
            display_config=display_config,
            no_text_debounce=no_text_debounce,
        )

    @classmethod
    def from_env(
        cls,
        process: ProcessHandler,
        on_reply_sent: OnReplySent = None,
    ) -> "WebhookChannel":
        return cls(
            process,
            enabled=_as_bool(os.environ.get("QWENPAW_WEBHOOK_ENABLED", "false")),
            default_target=os.environ.get("QWENPAW_WEBHOOK_DEFAULT_TARGET", ""),
            targets_file=os.environ.get("QWENPAW_WEBHOOK_TARGETS_FILE", ""),
            allow_raw_url=_as_bool(os.environ.get("QWENPAW_WEBHOOK_ALLOW_RAW_URL", "false")),
            inbound_enabled=_as_bool(os.environ.get("QWENPAW_WEBHOOK_INBOUND_ENABLED", "false")),
            inbound_secret=os.environ.get("QWENPAW_WEBHOOK_INBOUND_SECRET", ""),
            inbound_sources=os.environ.get("QWENPAW_WEBHOOK_INBOUND_SOURCES", ""),
            suppress_no_reply=_as_bool(
                os.environ.get("QWENPAW_WEBHOOK_SUPPRESS_NO_REPLY", "true"),
                True,
            ),
            timeout=float(os.environ.get("QWENPAW_WEBHOOK_TIMEOUT", "15") or 15),
            on_reply_sent=on_reply_sent,
        )

    async def start(self) -> None:
        if not self.enabled:
            return
        register_instance(self.agent_id, self)
        self._started = True
        logger.info("webhook channel started for agent=%s", self.agent_id)

    async def stop(self) -> None:
        unregister_instance(self.agent_id, self)
        self._started = False
        logger.info("webhook channel stopped for agent=%s", self.agent_id)

    def _effective_inbound_secret(self) -> str:
        return self.inbound_secret or os.environ.get(
            "QWENPAW_WEBHOOK_INBOUND_SECRET",
            "",
        ).strip()

    def verify_inbound_secret(self, supplied: str) -> bool:
        expected = self._effective_inbound_secret()
        if not expected or not supplied:
            return False
        return hmac.compare_digest(expected, supplied)

    def source_allowed(self, source: str) -> bool:
        return not self.inbound_sources or source in self.inbound_sources

    def resolve_session_id(
        self,
        sender_id: str,
        channel_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        meta = channel_meta or {}
        source = _safe_component(str(meta.get("webhook_source", "") or "source"), "source")
        sender = _safe_component(sender_id, "sender")
        return f"webhook:{source}:{sender}"

    def build_agent_request_from_native(self, native_payload: Any):
        payload = native_payload if isinstance(native_payload, dict) else {}
        sender_id = str(payload.get("sender_id") or "webhook")
        meta = dict(payload.get("meta") or {})
        session_id = str(payload.get("session_id") or "") or self.resolve_session_id(
            sender_id,
            meta,
        )
        content_parts = list(payload.get("content_parts") or [])
        request = self.build_agent_request_from_user_content(
            channel_id=self.channel,
            sender_id=sender_id,
            session_id=session_id,
            content_parts=content_parts,
            channel_meta=meta,
        )
        setattr(request, "channel_meta", meta)
        return request

    def get_to_handle_from_request(self, request: Any) -> str:
        meta = getattr(request, "channel_meta", None) or {}
        if meta.get("inbound_webhook"):
            return str(meta.get("reply_target") or "").strip()
        return (
            str(meta.get("reply_target") or "").strip()
            or str(getattr(request, "user_id", "") or "").strip()
            or self.default_target
        )

    def to_handle_from_target(self, *, user_id: str, session_id: str) -> str:
        del session_id
        return (user_id or "").strip() or self.default_target

    def enqueue_inbound(
        self,
        *,
        source: str,
        text: str,
        sender_id: str,
        session_id: str = "",
        reply_target: str = "",
    ) -> str:
        if not self.enabled or not self._started:
            raise RuntimeError("Webhook channel is not running")
        if self._enqueue is None:
            raise RuntimeError("Webhook channel queue is not ready")
        body = (text or "").strip()
        if not body:
            raise ValueError("Inbound webhook text is empty")

        meta = {
            "inbound_webhook": True,
            "webhook_source": source,
            "reply_target": (reply_target or "").strip(),
            "is_dm": True,
            "is_group": False,
        }
        sid = (session_id or "").strip() or self.resolve_session_id(sender_id, meta)
        native = {
            "channel_id": self.channel,
            "sender_id": sender_id,
            "session_id": sid,
            "content_parts": [TextContent(type=ContentType.TEXT, text=body)],
            "meta": meta,
        }
        self._enqueue(native)
        return sid

    async def send(
        self,
        to_handle: str,
        text: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        body = (text or "").strip()
        if not body:
            return
        if self.suppress_no_reply and body == "NO_REPLY":
            logger.info("webhook channel: suppressing NO_REPLY")
            return

        send_meta = meta or {}
        # Inbound webhooks are fire-and-forget by default. Only deliver the
        # Agent's final response when the caller explicitly supplied
        # reply_target. This avoids accidentally sending inbound responses to
        # default_target.
        if send_meta.get("inbound_webhook") and not str(
            send_meta.get("reply_target") or ""
        ).strip():
            logger.info("webhook channel: inbound reply has no reply_target; skipping")
            return

        target = (to_handle or "").strip() or self.default_target
        if not target:
            logger.info("webhook channel: no target, skipping delivery")
            return

        try:
            await send_webhook_request(
                target,
                content=body,
                data={"source": "qwenpaw-webhook-channel"},
                targets_file=self.targets_file,
                allow_raw_url=self.allow_raw_url,
                default_timeout=self.timeout,
            )
        except Exception:
            logger.exception("webhok channel delivery failed for target=%s", target[:64])

    async def health_check(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"channel": self.channel, "status": "unhealthy", "detail": "Channel disabled"}
        try:
            targets = load_targets(self.targets_file)
        except Exception as exc:
            return {
                "channel": self.channel,
                "status": "unhealthy",
                "detail": f"Target configuration error: {exc}",
            }
        detail = f"Loaded {len(targets)} webhook target(s)."
        if self.inbound_enabled and not self._effective_inbound_secret():
            return {
                "channel": self.channel,
                "status": "unhealthy",
                "detail": "Inbound enabled but no inbound secret is configured.",
            }
        return {"channel": self.channel, "status": "healthy", "detail": detail}
