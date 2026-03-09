from __future__ import annotations

import logging
import os
from typing import Any

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("qq_onebot_relay")
app = FastAPI(title="DGQ QQ OneBot Relay")


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _bool_env(name: str, default: bool = False) -> bool:
    value = _env(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


def _csv_int_set(name: str) -> set[str]:
    raw = _env(name)
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _backend_url() -> str:
    return _env("QQ_ONEBOT_RELAY_BACKEND_WEBHOOK_URL", "http://127.0.0.1:8000/api/connectors/qq/webhook")


def _onebot_api_base() -> str:
    return _env("QQ_ONEBOT_RELAY_ONEBOT_API_BASE_URL", "http://127.0.0.1:5700")


def _relay_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = _env("QQ_ONEBOT_RELAY_SHARED_TOKEN")
    if token:
        headers["X-Connector-Token"] = token
    return headers


def _onebot_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = _env("QQ_ONEBOT_RELAY_ONEBOT_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _allowed(payload: dict[str, Any]) -> tuple[bool, str]:
    post_type = str(payload.get("post_type") or "").strip().lower()
    message_type = str(payload.get("message_type") or "").strip().lower()
    if post_type != "message":
        return False, f"unsupported_post_type:{post_type or 'unknown'}"

    if message_type == "private" and not _bool_env("QQ_ONEBOT_RELAY_ALLOW_PRIVATE", False):
        return False, "private_message_disabled"

    if message_type == "group":
        allowed_group_ids = _csv_int_set("QQ_ONEBOT_RELAY_ALLOWED_GROUP_IDS")
        group_id = str(payload.get("group_id") or "").strip()
        if allowed_group_ids and group_id not in allowed_group_ids:
            return False, f"group_not_allowed:{group_id}"

    return True, "ok"


def _reply_via_onebot(payload: dict[str, Any], reply_message: str) -> dict[str, Any]:
    message_type = str(payload.get("message_type") or "").strip().lower()
    api_base = _onebot_api_base().rstrip("/")
    if message_type == "group":
        group_id = payload.get("group_id")
        if not group_id:
            return {"ok": False, "reason": "missing_group_id"}
        response = requests.post(
            f"{api_base}/send_group_msg",
            headers=_onebot_headers(),
            json={"group_id": int(group_id), "message": reply_message},
            timeout=10,
        )
        response.raise_for_status()
        return {"ok": True, "target": "group", "group_id": str(group_id), "result": response.json()}

    if message_type == "private":
        user_id = payload.get("user_id")
        if not user_id:
            return {"ok": False, "reason": "missing_user_id"}
        response = requests.post(
            f"{api_base}/send_private_msg",
            headers=_onebot_headers(),
            json={"user_id": int(user_id), "message": reply_message},
            timeout=10,
        )
        response.raise_for_status()
        return {"ok": True, "target": "private", "user_id": str(user_id), "result": response.json()}

    return {"ok": False, "reason": f"unsupported_message_type:{message_type or 'unknown'}"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def onebot_webhook(request: Request):
    payload = await request.json()
    ok, reason = _allowed(payload)
    if not ok:
        return {"ok": True, "action": "ignored", "reason": reason}

    backend_response = requests.post(
        _backend_url(),
        headers=_relay_headers(),
        json=payload,
        timeout=15,
    )
    backend_response.raise_for_status()
    body = backend_response.json()

    reply_message = str(body.get("reply_message") or "").strip()
    if not reply_message:
        return body

    try:
        send_result = _reply_via_onebot(payload, reply_message)
    except Exception as exc:
        logger.exception("reply via onebot failed")
        return JSONResponse(status_code=502, content={**body, "relay_send": {"ok": False, "reason": str(exc)}})

    return {**body, "relay_send": send_result}
