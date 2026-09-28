# -*- coding: utf-8 -*-
"""FastAPI endpoints for inbound webhooks."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .runtime import instance_status, select_instance
from .sender import (
    WebhookConfigError,
    load_file_targets,
    save_file_targets,
    send_webhook_request,
    validate_target_alias,
    validate_target_config,
)

router = APIRouter()
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _extract_plugin_secret(request: Request) -> str:
    supplied = request.headers.get("x-webhook-secret", "").strip()
    if supplied:
        return supplied
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _pick_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))





@router.get("/targets")
async def list_webhook_targets() -> dict:
    """List editable file-backed webhook targets."""
    try:
        path, targets = load_file_targets()
    except WebhookConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "path": str(path),
        "targets": targets,
    }


@router.put("/targets/{alias}")
async def put_webhook_target(alias: str, request: Request) -> dict:
    """Create or replace one file-backed webhook target."""
    try:
        name = validate_target_alias(alias)
        body = await request.json()
        if not isinstance(body, dict):
            raise WebhookConfigError("Target config must be a JSON object")
        config = validate_target_config(body)
        path, targets = load_file_targets()
        targets[name] = config
        save_file_targets(targets, str(path))
    except (json.JSONDecodeError, WebhookConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "alias": name}


@router.delete("/targets/{alias}")
async def delete_webhook_target(alias: str) -> dict:
    """Delete one file-backed webhook target."""
    try:
        name = validate_target_alias(alias)
        path, targets = load_file_targets()
        if name not in targets:
            raise HTTPException(status_code=404, detail="Webhook target not found")
        targets.pop(name, None)
        save_file_targets(targets, str(path))
    except WebhookConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "alias": name}


@router.post("/targets/{alias}/test")
async def test_webhook_target(alias: str, request: Request) -> dict:
    """Send a test message through one configured target."""
    try:
        name = validate_target_alias(alias)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        title = str(body.get("title") or "QwenPaw Webhook test")
        content = str(body.get("content") or "QwenPaw Webhook test message")
        data = body.get("data")
        if not isinstance(data, dict):
            data = {"test": True}
        result = await send_webhook_request(
            name,
            title=title,
            content=content,
            data=data,
        )
    except WebhookConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ok": True,
        "target": result.target,
        "status_code": result.status_code,
        "response_preview": result.response_preview,
    }

@router.get("/health")
async def webhook_health() -> dict:
    status = instance_status()
    return {
        "ok": True,
        "channel_instances": status["count"],
        "agents": status["agents"],
    }


@router.post("/inbound/{source}")
async def inbound_webhook(source: str, request: Request):
    if not _SOURCE_RE.fullmatch(source):
        raise HTTPException(status_code=400, detail="Invalid webhook source name")

    requested_agent = (
        request.headers.get("x-agent-id", "").strip()
        or request.query_params.get("agent_id", "").strip()
    )
    agent_id, channel = select_instance(requested_agent)
    if channel is None:
        status = instance_status()
        if requested_agent:
            detail = f"No running webhook channel for agent '{requested_agent}'"
        elif status["count"] > 1:
            detail = "Multiple webhook channel instances are running; provide X-Agent-Id"
        else:
            detail = "No running webhook channel instance; enable the Webhook channel"
        raise HTTPException(status_code=503, detail=detail)

    if not channel.inbound_enabled:
        raise HTTPException(status_code=403, detail="Inbound webhook is disabled")
    if not channel.source_allowed(source):
        raise HTTPException(status_code=403, detail="Webhook source is not allowed")
    if not channel.verify_inbound_secret(_extract_plugin_secret(request)):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    content_length = request.headers.get("content-length", "").strip()
    if content_length.isdigit() and int(content_length) > channel.max_inbound_bytes:
        raise HTTPException(status_code=413, detail="Webhook body too large")
    raw = await request.body()
    if len(raw) > channel.max_inbound_bytes:
        raise HTTPException(status_code=413, detail="Webhook body too large")
    if not raw:
        raise HTTPException(status_code=400, detail="Webhook body is empty")

    content_type = request.headers.get("content-type", "").lower()
    payload: dict[str, Any]
    if "application/json" in content_type:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        if isinstance(parsed, dict):
            payload = parsed
        else:
            payload = {"content": json.dumps(parsed, ensure_ascii=False)}
    else:
        payload = {"content": raw.decode("utf-8", errors="replace")}

    text = _pick_text(payload).strip()
    if not text:
        raise HTTPException(status_code=400, detail="No usable message content")

    sender_id = str(payload.get("sender_id") or payload.get("user_id") or source).strip()
    session_id = str(payload.get("session_id") or "").strip()
    reply_target = str(payload.get("reply_target") or "").strip()

    try:
        accepted_session = channel.enqueue_inbound(
            source=source,
            text=text,
            sender_id=sender_id,
            session_id=session_id,
            reply_target=reply_target,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "agent_id": agent_id,
            "session_id": accepted_session,
        },
    )
