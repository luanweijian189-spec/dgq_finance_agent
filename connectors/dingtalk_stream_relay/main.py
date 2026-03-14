from __future__ import annotations

import json
import logging
import os
from typing import Any

import dingtalk_stream
import requests
from dingtalk_stream import AckMessage

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


logger = logging.getLogger("dingtalk_stream_relay")


def _load_local_env() -> None:
    if load_dotenv is None:
        return
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env_path = os.path.join(root_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=False)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _bool_env(name: str, default: bool = False) -> bool:
    value = _env(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


def _csv_set(name: str) -> set[str]:
    raw = _env(name)
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _client_id() -> str:
    return _env("DINGTALK_STREAM_CLIENT_ID") or _env("DINGTALK_CLIENT_ID")


def _client_secret() -> str:
    return _env("DINGTALK_STREAM_CLIENT_SECRET") or _env("DINGTALK_CLIENT_SECRET")


def _backend_url() -> str:
    return _env("DINGTALK_STREAM_BACKEND_WEBHOOK_URL", "http://127.0.0.1:8000/api/connectors/dingtalk/webhook")


def _relay_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = _env("DINGTALK_STREAM_SHARED_TOKEN") or _env("CONNECTOR_SHARED_TOKEN")
    if token:
        headers["X-Connector-Token"] = token
    return headers


def _stream_subscriptions() -> list[dict[str, str]]:
    return [{"type": "CALLBACK", "topic": dingtalk_stream.ChatbotMessage.TOPIC}]


def _preflight_open_connection(client_id: str, client_secret: str) -> None:
    url = dingtalk_stream.DingTalkStreamClient.OPEN_CONNECTION_API
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "subscriptions": _stream_subscriptions(),
        "ua": "dgq-finance-agent-dingtalk-relay-preflight",
        "localIp": "127.0.0.1",
    }
    response = requests.post(url, headers=headers, data=json.dumps(body), timeout=20)
    if response.ok:
        logger.info("dingtalk stream preflight ok")
        return

    detail = response.text.strip()
    request_id = ""
    code = ""
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            request_id = str(payload.get("requestid") or "").strip()
            code = str(payload.get("code") or "").strip()
            message = str(payload.get("message") or "").strip()
    except Exception:
        pass

    if response.status_code == 400 and code == "systemError":
        raise SystemExit(
            "钉钉 Stream 预检失败：gateway/connections/open 返回 400 systemError。\n"
            "这通常不是代码或密钥错误，而是开放平台侧还没把当前应用的 Stream 事件订阅真正配置完成。\n"
            "请到钉钉开放平台 -> 当前企业内部应用 -> 开发配置 -> 事件订阅：\n"
            "1. 选择 Stream 模式推送；\n"
            "2. 点击“已完成接入/验证连接通道”；\n"
            "3. 保存事件订阅配置；\n"
            "4. 确认机器人消息接收能力已启用，并把机器人加入目标群；\n"
            f"requestid={request_id or '-'} code={code or '-'} message={message or detail}"
        )

    raise SystemExit(
        f"钉钉 Stream 预检失败：status={response.status_code} response={detail}"
    )


def _allowed(payload: dict[str, Any]) -> tuple[bool, str]:
    conversation_type = str(payload.get("conversationType") or "").strip()
    conversation_id = str(payload.get("conversationId") or "").strip()
    allowed_conversation_ids = _csv_set("DINGTALK_STREAM_ALLOWED_CONVERSATION_IDS")

    if allowed_conversation_ids and conversation_id not in allowed_conversation_ids:
        return False, f"conversation_not_allowed:{conversation_id or 'unknown'}"

    if conversation_type == "2" and not _bool_env("DINGTALK_STREAM_ALLOW_GROUP_CHAT", True):
        return False, "group_chat_disabled"
    if conversation_type == "1" and not _bool_env("DINGTALK_STREAM_ALLOW_SINGLE_CHAT", True):
        return False, "single_chat_disabled"
    if conversation_type == "2" and _bool_env("DINGTALK_STREAM_PROCESS_ONLY_AT_MESSAGES", True):
        if not bool(payload.get("isInAtList")):
            return False, "group_message_not_at_bot"
    return True, "ok"


def _extract_message_text(payload: dict[str, Any]) -> str:
    text = payload.get("text") if isinstance(payload.get("text"), dict) else {}
    content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    if isinstance(text, dict):
        value = str(text.get("content") or "").strip()
        if value:
            return value
    if isinstance(content, dict):
        value = str(content.get("content") or "").strip()
        if value:
            return value
    return str(payload.get("message") or payload.get("content") or "").strip()


def _to_backend_payload(payload: dict[str, Any]) -> dict[str, Any]:
    conversation_type = str(payload.get("conversationType") or "").strip()
    event_type = "group_message" if conversation_type == "2" else "private_message"
    message = _extract_message_text(payload)
    sender_id = str(payload.get("senderStaffId") or payload.get("senderId") or "").strip()
    conversation_id = str(payload.get("conversationId") or "").strip()
    conversation_title = str(payload.get("conversationTitle") or "").strip()
    sender_nick = str(payload.get("senderNick") or sender_id or "未知用户").strip()
    return {
        "channel": "dingtalk",
        "source": "dingtalk_stream",
        "event_type": event_type,
        "message": message,
        "text": message,
        "sender_name": sender_nick,
        "sender_id": sender_id,
        "group_name": conversation_title,
        "group_id": conversation_id,
        "conversation_id": conversation_id,
        "room_topic": conversation_title,
        "senderNick": sender_nick,
        "senderStaffId": sender_id,
        "conversationId": conversation_id,
        "conversationTitle": conversation_title,
        "conversationType": conversation_type,
        "raw_payload": payload,
    }


class RelayHandler(dingtalk_stream.ChatbotHandler):
    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming_message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        payload = incoming_message.to_dict()
        logger.info(
            "dingtalk callback received: conversation=%s type=%s sender=%s at=%s msgtype=%s text=%s",
            payload.get("conversationId"),
            payload.get("conversationType"),
            payload.get("senderNick") or payload.get("senderStaffId") or payload.get("senderId"),
            payload.get("isInAtList"),
            payload.get("msgtype"),
            _extract_message_text(payload),
        )
        ok, reason = _allowed(payload)
        if not ok:
            logger.info("ignore dingtalk message: %s", reason)
            return AckMessage.STATUS_OK, "OK"

        backend_response = requests.post(
            _backend_url(),
            headers=_relay_headers(),
            json=_to_backend_payload(payload),
            timeout=20,
        )
        backend_response.raise_for_status()
        body = backend_response.json()

        reply_message = str(body.get("reply_message") or "").strip()
        if reply_message and _bool_env("DINGTALK_STREAM_REPLY_ENABLED", True):
            self.reply_text(reply_message, incoming_message)

        logger.info(
            "dingtalk relay handled: action=%s conversation=%s sender=%s",
            body.get("action"),
            payload.get("conversationId"),
            payload.get("senderNick"),
        )
        return AckMessage.STATUS_OK, "OK"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _load_local_env()
    client_id = _client_id()
    client_secret = _client_secret()
    if not client_id or not client_secret:
        raise SystemExit("missing DINGTALK_STREAM_CLIENT_ID / DINGTALK_STREAM_CLIENT_SECRET")

    _preflight_open_connection(client_id, client_secret)

    credential = dingtalk_stream.Credential(client_id, client_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, RelayHandler())
    client.start_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())