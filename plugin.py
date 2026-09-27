# -*- coding: utf-8 -*-
"""QwenPaw Webhook plugin entry point."""

from __future__ import annotations

import logging

from qwenpaw.plugins.api import PluginApi

from .channel import WebhookChannel
from .router import router
from .tool import send_webhook

logger = logging.getLogger(__name__)


class QwenPawWebhookPlugin:
    """Register webhook Tool + HTTP API + Channel."""

    def register(self, api: PluginApi) -> None:
        api.register_tool(
            tool_name="send_webhook",
            tool_func=send_webhook,
            description=(
                "Send a notification to a trusted preconfigured webhook target alias. "
                "Use only when external delivery is actually required."
            ),
            icon="🔗",
            enabled=False,
            tool_type="network",
            target_param="target",
        )

        api.register_http_router(
            router,
            prefix="/webhook",
            tags=["webhook"],
        )

        api.register_channel(
            channel_class=WebhookChannel,
            label="Webhook",
            description=(
                "Generic inbound/outbound webhook channel with inbound triggers, "
                "outbound delivery, and NO_REPLY suppression."
            ),
            config_fields=[
                {
                    "name": "default_target",
                    "label": {"zh-CN": "默认目标别名", "en-US": "Default target alias"},
                    "type": "text",
                    "required": False,
                    "placeholder": "notify",
                    "help": {"zh-CN": "对应 webhook_targets.json 中的目标名。", "en-US": "Alias from webhook_targets.json."},
                },
                {
                    "name": "targets_file",
                    "label": {"zh-CN": "目标配置文件", "en-US": "Targets file"},
                    "type": "text",
                    "required": False,
                    "placeholder": "/path/to/webhook_targets.json",
                    "help": {"zh-CN": "留空时使用 QWENPAW_WEBHOOK_TARGETS_FILE 或 QwenPaw Secret 目录默认文件。", "en-US": "Leave empty to use env/default secret path."},
                },
                {
                    "name": "allow_raw_url",
                    "label": {"zh-CN": "允许直接 URL", "en-US": "Allow raw URLs"},
                    "type": "switch",
                    "required": False,
                    "default": False,
                    "help": {"zh-CN": "建议关闭，只允许管理员预配置的目标别名。", "en-US": "Recommended off; use trusted aliases instead."},
                },
                {
                    "name": "inbound_enabled",
                    "label": {"zh-CN": "启用入站 Webhook", "en-US": "Enable inbound webhook"},
                    "type": "switch",
                    "required": False,
                    "default": False,
                },
                {
                    "name": "inbound_secret",
                    "label": {"zh-CN": "入站密钥", "en-US": "Inbound secret"},
                    "type": "password",
                    "required": False,
                    "placeholder": "use-a-long-random-secret",
                    "help": {"zh-CN": "也可通过 QWENPAW_WEBHOOK_INBOUND_SECRET 提供。", "en-US": "Can also be provided via QWENPAW_WEBHOOK_INBOUND_SECRET."},
                },
                {
                    "name": "inbound_sources",
                    "label": {"zh-CN": "允许来源", "en-US": "Allowed sources"},
                    "type": "text",
                    "required": False,
                    "placeholder": "uptime-kuma,github,beszel",
                    "help": {"zh-CN": "逗号分隔；留空允许所有通过密钥认证的来源。", "en-US": "Comma-separated; empty allows all authenticated sources."},
                },
                {
                    "name": "suppress_no_reply",
                    "label": {"zh-CN": "抑制 NO_REPLY", "en-US": "Suppress NO_REPLY"},
                    "type": "switch",
                    "required": False,
                    "default": True,
                    "help": {"zh-CN": "开启后，模型最终输出恰好为 NO_REPLY 时不会调用 Webhook。", "en-US": "Do not deliver when final text is exactly NO_REPLY."},
                },
                {
                    "name": "timeout",
                    "label": {"zh-CN": "请求超时（秒）", "en-US": "Request timeout (seconds)"},
                    "type": "number",
                    "required": False,
                    "default": 15,
                },
                {
                    "name": "max_inbound_bytes",
                    "label": {"zh-CN": "入站最大字节数", "en-US": "Max inbound bytes"},
                    "type": "number",
                    "required": False,
                    "default": 262144,
                },
            ],
        )

        logger.info("QwenPaw Webhook plugin registered")


plugin = QwenPawWebhookPlugin()
