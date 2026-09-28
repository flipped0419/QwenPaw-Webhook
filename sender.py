# -*- coding: utf-8 -*-
"""Outbound webhook target loading, templating and delivery."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import httpx

from qwenpaw.constant import SECRET_DIR

logger = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DEFAULT_TIMEOUT = 15.0
_MAX_CONTENT_CHARS = 64_000
_TARGET_ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class WebhookConfigError(ValueError):
    """Invalid webhook configuration or target."""


@dataclass(slots=True)
class SendResult:
    target: str
    status_code: int
    response_preview: str


def _expand_env_string(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    return _ENV_RE.sub(repl, value)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_env_string(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _expand_env(v) for k, v in value.items()}
    return value


def default_targets_file() -> Path:
    env_path = os.environ.get("QWENPAW_WEBHOOK_TARGETS_FILE", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path(SECRET_DIR) / "webhook_targets.json"


def _load_json_object(raw: str, source: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebhookConfigError(f"Invalid JSON in {source}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise WebhookConfigError(f"{source} must contain a JSON object")
    return parsed


def validate_target_alias(alias: str) -> str:
    """Validate and normalize a persisted webhook target alias."""
    value = (alias or "").strip()
    if not _TARGET_ALIAS_RE.fullmatch(value):
        raise WebhookConfigError(
            "Target alias must be 1-64 characters using letters, numbers, '.', '_' or '-'"
        )
    return value


def load_file_targets(targets_file: str = "") -> tuple[Path, dict[str, dict[str, Any]]]:
    """Load only the editable file-backed target definitions.

    Environment-provided targets are intentionally excluded because the
    management UI must never rewrite QWENPAW_WEBHOOK_TARGETS_JSON.
    """
    path = Path(targets_file).expanduser() if targets_file else default_targets_file()
    if not path.exists():
        return path, {}
    if not path.is_file():
        raise WebhookConfigError(f"Targets path is not a file: {path}")
    try:
        raw = _load_json_object(path.read_text("utf-8"), str(path))
    except OSError as exc:
        raise WebhookConfigError(f"Cannot read targets file: {exc}") from exc

    result: dict[str, dict[str, Any]] = {}
    for alias, cfg in raw.items():
        name = validate_target_alias(str(alias))
        if isinstance(cfg, str):
            cfg = {"url": cfg}
        if not isinstance(cfg, dict):
            raise WebhookConfigError(f"Target '{name}' must be an object or URL string")
        result[name] = dict(cfg)
    return path, result


def save_file_targets(
    targets: Mapping[str, Mapping[str, Any]],
    targets_file: str = "",
) -> Path:
    """Atomically save editable webhook targets with restrictive permissions."""
    path = Path(targets_file).expanduser() if targets_file else default_targets_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    normalized: dict[str, dict[str, Any]] = {}
    for alias, cfg in targets.items():
        name = validate_target_alias(str(alias))
        if not isinstance(cfg, Mapping):
            raise WebhookConfigError(f"Target '{name}' must be an object")
        normalized[name] = dict(cfg)

    payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise WebhookConfigError(f"Cannot write targets file: {exc}") from exc
    return path


def validate_target_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a target definition without sending a request."""
    if not isinstance(cfg, Mapping):
        raise WebhookConfigError("Target config must be a JSON object")
    value = dict(cfg)
    raw_url = str(value.get("url", "") or "").strip()
    if not raw_url:
        raise WebhookConfigError("Webhook URL is empty")
    # Allow ${ENV_NAME} placeholders to be stored even when the variable is
    # not present in this process yet. Runtime delivery validates the expanded
    # URL again before any network request is made.
    if "${" not in raw_url:
        _validate_url(raw_url)
    else:
        expanded_url = _expand_env_string(raw_url).strip()
        if expanded_url:
            _validate_url(expanded_url)
    method = str(value.get("method", "POST") or "POST").strip().upper()
    if method not in {"POST", "PUT", "PATCH"}:
        raise WebhookConfigError("Webhook method must be POST, PUT, or PATCH")
    value["method"] = method

    fmt = str(value.get("format", "json") or "json").strip().lower()
    if fmt not in {"json", "form", "text"}:
        raise WebhookConfigError("Target format must be one of: json, form, text")
    value["format"] = fmt

    headers = value.get("headers", {})
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise WebhookConfigError("Target headers must be a JSON object")
    value["headers"] = headers

    try:
        timeout = float(value.get("timeout", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT)
    except (TypeError, ValueError) as exc:
        raise WebhookConfigError("Target timeout must be a number") from exc
    value["timeout"] = max(1.0, min(timeout, 120.0))
    value["verify_tls"] = bool(value.get("verify_tls", True))

    # Validate payload shape using harmless placeholder content.
    _build_payload(value, title="test", content="test", data={})
    return value


def load_targets(targets_file: str = "") -> dict[str, dict[str, Any]]:
    """Load trusted target aliases from file and optional environment JSON.

    File targets are loaded first. QWENPAW_WEBHOOK_TARGETS_JSON overrides
    entries with the same alias. Environment references such as ${TOKEN}
    are expanded after merging.
    """
    path = Path(targets_file).expanduser() if targets_file else default_targets_file()
    merged: dict[str, Any] = {}

    if path.is_file():
        try:
            merged.update(_load_json_object(path.read_text("utf-8"), str(path)))
        except OSError as exc:
            raise WebhookConfigError(f"Cannot read targets file: {exc}") from exc

    env_json = os.environ.get("QWENPAW_WEBHOOK_TARGETS_JSON", "").strip()
    if env_json:
        merged.update(_load_json_object(env_json, "QWENPAW_WEBHOOK_TARGETS_JSON"))

    expanded = _expand_env(merged)
    result: dict[str, dict[str, Any]] = {}
    for alias, cfg in expanded.items():
        if not isinstance(alias, str) or not alias.strip():
            continue
        if isinstance(cfg, str):
            cfg = {"url": cfg}
        if not isinstance(cfg, dict):
            raise WebhookConfigError(f"Target '{alias}' must be an object or URL string")
        result[alias.strip()] = dict(cfg)
    return result


def _normalize_target_name(target: str) -> str:
    value = (target or "").strip()
    if value.startswith("webhook:"):
        value = value[len("webhook:") :].strip()
    return value


def resolve_target(
    target: str,
    *,
    targets_file: str = "",
    allow_raw_url: bool = False,
) -> tuple[str, dict[str, Any]]:
    name = _normalize_target_name(target)
    if not name:
        raise WebhookConfigError("Webhook target is empty")

    targets = load_targets(targets_file)
    if name in targets:
        cfg = dict(targets[name])
        cfg.setdefault("_alias", name)
        return name, cfg

    if name.startswith(("https://", "http://")):
        if not allow_raw_url:
            raise WebhookConfigError(
                "Raw webhook URLs are disabled; configure a trusted target alias instead"
            )
        return "raw-url", {"url": name, "_alias": "raw-url"}

    raise WebhookConfigError(f"Unknown webhook target alias: {name}")


def _validate_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebhookConfigError("Webhook URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise WebhookConfigError("Credentials in webhook URL are not allowed")
    return value


def _render_string(value: str, *, title: str, content: str, data: dict[str, Any]) -> str:
    replacements = {
        "{{title}}": title,
        "{{content}}": content,
        "{{data_json}}": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    }
    out = value
    for key, replacement in replacements.items():
        out = out.replace(key, replacement)
    return out


def _render_template(value: Any, *, title: str, content: str, data: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value == "{{data}}":
            return data
        return _render_string(value, title=title, content=content, data=data)
    if isinstance(value, list):
        return [
            _render_template(item, title=title, content=content, data=data)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(k): _render_template(v, title=title, content=content, data=data)
            for k, v in value.items()
        }
    return value


def _build_payload(
    cfg: Mapping[str, Any],
    *,
    title: str,
    content: str,
    data: dict[str, Any],
) -> tuple[str, Any]:
    fmt = str(cfg.get("format", "json") or "json").strip().lower()
    template = cfg.get("payload")

    if template is None:
        if fmt == "text":
            template = "{{content}}"
        elif fmt == "form":
            template = {"title": "{{title}}", "content": "{{content}}"}
        else:
            template = {
                "title": "{{title}}",
                "content": "{{content}}",
                "data": "{{data}}",
            }

    rendered = _render_template(template, title=title, content=content, data=data)
    if fmt not in {"json", "form", "text"}:
        raise WebhookConfigError("Target format must be one of: json, form, text")
    if fmt == "form" and not isinstance(rendered, dict):
        raise WebhookConfigError("form payload must render to an object")
    if fmt == "text" and not isinstance(rendered, str):
        rendered = json.dumps(rendered, ensure_ascii=False)
    return fmt, rendered


async def send_webhook_request(
    target: str,
    *,
    content: str,
    title: str = "",
    data: Optional[dict[str, Any]] = None,
    targets_file: str = "",
    allow_raw_url: bool = False,
    default_timeout: float = _DEFAULT_TIMEOUT,
) -> SendResult:
    """Resolve a configured target and send one webhook request."""
    body = (content or "").strip()
    if not body:
        raise WebhookConfigError("Webhook content is empty")
    if len(body) > _MAX_CONTENT_CHARS:
        raise WebhookConfigError(
            f"Webhook content exceeds {_MAX_CONTENT_CHARS} characters"
        )

    alias, cfg = resolve_target(
        target,
        targets_file=targets_file,
        allow_raw_url=allow_raw_url,
    )
    url = _validate_url(str(cfg.get("url", "")))
    method = str(cfg.get("method", "POST") or "POST").strip().upper()
    if method not in {"POST", "PUT", "PATCH"}:
        raise WebhookConfigError("Webhook method must be POST, PUT, or PATCH")

    headers_raw = cfg.get("headers") or {}
    if not isinstance(headers_raw, dict):
        raise WebhookConfigError("Target headers must be a JSON object")
    headers = {str(k): str(v) for k, v in headers_raw.items()}

    try:
        timeout = float(cfg.get("timeout", default_timeout) or default_timeout)
    except (TypeError, ValueError):
        timeout = default_timeout
    timeout = max(1.0, min(timeout, 120.0))
    verify_tls = bool(cfg.get("verify_tls", True))
    fmt, payload = _build_payload(
        cfg,
        title=(title or "").strip(),
        content=body,
        data=data or {},
    )

    request_kwargs: dict[str, Any] = {"headers": headers}
    if fmt == "json":
        request_kwargs["json"] = payload
    elif fmt == "form":
        request_kwargs["data"] = payload
    else:
        request_kwargs["content"] = payload.encode("utf-8")
        request_kwargs["headers"] = {"Content-Type": "text/plain; charset=utf-8", **headers}

    # Deliberately do not log the URL or headers: they may contain secrets.
    logger.info("webhook send: target=%s method=%s", alias, method)
    async with httpx.AsyncClient(
        timeout=timeout,
        verify=verify_tls,
        follow_redirects=False,
    ) as client:
        response = await client.request(method, url, **request_kwargs)

    preview = response.text[:500] if response.text else ""
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(
            f"Webhook target '{alias}' returned HTTP {response.status_code}"
        )
    return SendResult(
        target=alias,
        status_code=response.status_code,
        response_preview=preview,
    )
