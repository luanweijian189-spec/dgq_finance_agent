from __future__ import annotations

import binascii
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import requests
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey


logger = logging.getLogger(__name__)

QQ_CALLBACK_DISPATCH = 0
QQ_CALLBACK_HEARTBEAT = 1
QQ_CALLBACK_HEARTBEAT_ACK = 11
QQ_CALLBACK_ACK = 12
QQ_CALLBACK_VALIDATION = 13

QQ_HEADER_SIGNATURE = "X-Signature-Ed25519"
QQ_HEADER_TIMESTAMP = "X-Signature-Timestamp"
QQ_HEADER_UNION_APPID = "X-Union-Appid"

QQ_EVENT_GROUP_AT_MESSAGE_CREATE = "GROUP_AT_MESSAGE_CREATE"
QQ_EVENT_C2C_MESSAGE_CREATE = "C2C_MESSAGE_CREATE"

_AT_RE = re.compile(r"<@!?[^>]+>")


def _first_non_empty(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _build_secret_seed(secret: str) -> bytes:
    seed = str(secret or "")
    if not seed:
        raise ValueError("qq official bot secret is empty")
    while len(seed) < 32:
        seed = seed * 2
    return seed[:32].encode("utf-8")


def _build_signing_key(secret: str) -> SigningKey:
    return SigningKey(_build_secret_seed(secret))


def _signature_payload(timestamp: str, body: bytes) -> bytes:
    if not timestamp:
        raise ValueError("missing signature timestamp")
    return timestamp.encode("utf-8") + body


def verify_qq_signature(secret: str, headers: dict[str, str] | Any, body: bytes) -> bool:
    signature_hex = _first_non_empty(
        getattr(headers, "get", lambda *_args, **_kwargs: "")(QQ_HEADER_SIGNATURE),
        getattr(headers, "get", lambda *_args, **_kwargs: "")(QQ_HEADER_SIGNATURE.lower()),
    )
    timestamp = _first_non_empty(
        getattr(headers, "get", lambda *_args, **_kwargs: "")(QQ_HEADER_TIMESTAMP),
        getattr(headers, "get", lambda *_args, **_kwargs: "")(QQ_HEADER_TIMESTAMP.lower()),
    )
    if not signature_hex or not timestamp:
        return False

    try:
        signature = binascii.unhexlify(signature_hex)
        verify_key = _build_signing_key(secret).verify_key
        verify_key.verify(_signature_payload(timestamp, body), signature)
        return True
    except (ValueError, binascii.Error, BadSignatureError):
        return False


def generate_qq_signature(secret: str, timestamp: str, body: bytes) -> str:
    signing_key = _build_signing_key(secret)
    signed = signing_key.sign(_signature_payload(timestamp, body))
    return signed.signature.hex()


def build_validation_response(secret: str, plain_token: str, event_ts: str) -> dict[str, str]:
    return {
        "plain_token": plain_token,
        "signature": generate_qq_signature(secret, event_ts, plain_token.encode("utf-8")),
    }


def build_heartbeat_ack(seq: int) -> dict[str, int]:
    return {"op": QQ_CALLBACK_HEARTBEAT_ACK, "d": int(seq)}


def build_dispatch_ack(success: bool = True) -> dict[str, int]:
    return {"op": QQ_CALLBACK_ACK, "d": 0 if success else 1}


def clean_qq_message_text(content: str) -> str:
    text = _AT_RE.sub(" ", str(content or ""))
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


@dataclass(slots=True)
class QQOfficialMessageEvent:
    event_type: str
    event_id: str
    message_id: str
    chat_type: str
    chat_id: str
    sender_id: str
    sender_name: str
    room_topic: str
    content: str
    clean_content: str
    is_bot: bool = False

    def to_connector_payload(self) -> dict[str, str]:
        source = "qq_official_group" if self.chat_type == "group" else "qq_official_c2c"
        event_type = "group_message" if self.chat_type == "group" else "private_message"
        return {
            "channel": "qq",
            "source": source,
            "message": self.clean_content,
            "raw_message": self.content,
            "recommender_name": self.sender_name,
            "sender_id": self.sender_id,
            "room_topic": self.room_topic,
            "conversation_id": self.chat_id,
            "event_type": event_type,
        }


def parse_qq_callback_body(body: bytes) -> dict[str, Any]:
    response = requests.models.complexjson.loads(body.decode("utf-8"))
    if not isinstance(response, dict):
        raise ValueError("invalid qq official callback payload")
    return response


def parse_qq_message_event(payload: dict[str, Any]) -> QQOfficialMessageEvent | None:
    event_type = str(payload.get("t") or "").strip().upper()
    if event_type not in {QQ_EVENT_GROUP_AT_MESSAGE_CREATE, QQ_EVENT_C2C_MESSAGE_CREATE}:
        return None

    data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    member = data.get("member") if isinstance(data.get("member"), dict) else {}

    content = _first_non_empty(data.get("content"), data.get("message"), data.get("text"))
    clean_content = clean_qq_message_text(content)
    sender_id = _first_non_empty(
        author.get("user_openid"),
        author.get("openid"),
        author.get("id"),
        data.get("user_openid"),
        data.get("openid"),
    )
    sender_name = _first_non_empty(
        member.get("nick"),
        author.get("username"),
        author.get("nick"),
        sender_id,
        "未知用户",
    )
    is_bot = str(author.get("bot") or "").strip().lower() in {"1", "true", "yes"}

    if event_type == QQ_EVENT_GROUP_AT_MESSAGE_CREATE:
        chat_type = "group"
        chat_id = _first_non_empty(data.get("group_id"), data.get("group_openid"))
        room_topic = _first_non_empty(data.get("group_name"), data.get("channel_name"), "QQ群")
    else:
        chat_type = "private"
        chat_id = _first_non_empty(
            author.get("user_openid"),
            author.get("openid"),
            author.get("id"),
            data.get("user_openid"),
            data.get("openid"),
        )
        room_topic = _first_non_empty(sender_name, "QQ私聊")

    return QQOfficialMessageEvent(
        event_type=event_type,
        event_id=str(payload.get("id") or "").strip(),
        message_id=_first_non_empty(data.get("id"), payload.get("id")),
        chat_type=chat_type,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
        room_topic=room_topic,
        content=content,
        clean_content=clean_content,
        is_bot=is_bot,
    )


class QQOfficialBotClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        api_base_url: str = "https://api.sgroup.qq.com",
        token_url: str = "https://bots.qq.com/app/getAppAccessToken",
        timeout_seconds: int = 10,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.api_base_url = str(api_base_url or "https://api.sgroup.qq.com").rstrip("/")
        self.token_url = str(token_url or "https://bots.qq.com/app/getAppAccessToken").strip()
        self.timeout_seconds = max(int(timeout_seconds or 10), 3)
        self._token_lock = threading.Lock()
        self._access_token = ""
        self._access_token_expiry = datetime.min.replace(tzinfo=timezone.utc)
        self._session = requests.Session()

    def _token_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self._access_token_expiry

    def get_access_token(self) -> str:
        if self._access_token and not self._token_expired():
            return self._access_token

        with self._token_lock:
            if self._access_token and not self._token_expired():
                return self._access_token

            response = self._session.post(
                self.token_url,
                json={"appId": self.app_id, "clientSecret": self.app_secret},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            if int(data.get("code") or 0) != 0:
                raise RuntimeError(f"qq official token request failed: {data}")

            token = str(data.get("access_token") or "").strip()
            expires_in = int(str(data.get("expires_in") or "0") or 0)
            if not token or expires_in <= 0:
                raise RuntimeError(f"qq official token response invalid: {data}")

            self._access_token = token
            self._access_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 9, 1))
            return self._access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"QQBot {self.get_access_token()}",
            QQ_HEADER_UNION_APPID: self.app_id,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        response = self._session.request(
            method=method,
            url=f"{self.api_base_url}{path}",
            headers=self._headers(),
            json=json_body,
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                detail = response.text.strip()
            except Exception:
                detail = ""
            raise requests.HTTPError(
                f"{exc} | response={detail}",
                response=response,
                request=response.request,
            ) from exc
        if not response.content:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}

    def send_text(
        self,
        target_type: str,
        target_id: str,
        content: str,
        reply_to_message_id: str = "",
        event_id: str = "",
        msg_seq: int = 1,
    ) -> dict[str, Any]:
        target = str(target_type or "group").strip().lower()
        target_value = str(target_id or "").strip()
        if target not in {"group", "private", "c2c"}:
            raise ValueError(f"unsupported qq official target type: {target_type}")
        if not target_value:
            raise ValueError("qq official target_id is empty")

        payload: dict[str, Any] = {
            "content": str(content or "").strip(),
            "msg_type": 0,
        }
        if reply_to_message_id:
            payload["msg_id"] = reply_to_message_id
            payload["msg_seq"] = max(int(msg_seq or 1), 1)
            payload["message_reference"] = {
                "message_id": reply_to_message_id,
                "ignore_get_message_error": True,
            }
        elif event_id:
            payload["event_id"] = event_id
            payload["msg_seq"] = max(int(msg_seq or 1), 1)

        if target == "group":
            return self._request("POST", f"/v2/groups/{target_value}/messages", payload)
        return self._request("POST", f"/v2/users/{target_value}/messages", payload)


@lru_cache(maxsize=4)
def get_qq_official_bot_client(
    app_id: str,
    app_secret: str,
    api_base_url: str,
    token_url: str,
    timeout_seconds: int,
) -> QQOfficialBotClient:
    return QQOfficialBotClient(
        app_id=app_id,
        app_secret=app_secret,
        api_base_url=api_base_url,
        token_url=token_url,
        timeout_seconds=timeout_seconds,
    )