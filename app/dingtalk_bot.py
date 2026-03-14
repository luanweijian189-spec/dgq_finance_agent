from __future__ import annotations

import json
import threading
from functools import lru_cache
from time import time

import requests


class DingTalkBotClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        robot_code: str,
        api_base_url: str = "https://api.dingtalk.com",
        oauth_url: str = "https://api.dingtalk.com/v1.0/oauth2/accessToken",
        timeout_seconds: int = 10,
    ) -> None:
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.robot_code = str(robot_code or "").strip()
        self.api_base_url = str(api_base_url or "https://api.dingtalk.com").strip().rstrip("/")
        self.oauth_url = str(oauth_url or "https://api.dingtalk.com/v1.0/oauth2/accessToken").strip()
        self.timeout_seconds = max(int(timeout_seconds or 10), 3)
        self._lock = threading.Lock()
        self._access_token = ""
        self._expires_at = 0.0

    def get_access_token(self, force_refresh: bool = False) -> str:
        if not self.client_id or not self.client_secret:
            raise ValueError("dingtalk client missing client_id or client_secret")

        now = time()
        with self._lock:
            if not force_refresh and self._access_token and now < self._expires_at:
                return self._access_token

            response = requests.post(
                self.oauth_url,
                json={"appKey": self.client_id, "appSecret": self.client_secret},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            access_token = str(payload.get("accessToken") or "").strip()
            expire_in = int(payload.get("expireIn") or 7200)
            if not access_token:
                raise ValueError(f"dingtalk access token missing: {payload}")

            self._access_token = access_token
            self._expires_at = now + max(expire_in - 120, 60)
            return self._access_token

    def _request(self, method: str, path: str, *, json_body: dict[str, object]) -> dict[str, object]:
        token = self.get_access_token()
        url = f"{self.api_base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": token,
        }
        response = requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            timeout=self.timeout_seconds,
        )
        if response.status_code == 401:
            token = self.get_access_token(force_refresh=True)
            headers["x-acs-dingtalk-access-token"] = token
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=self.timeout_seconds,
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise requests.HTTPError(
                f"{exc} | dingtalk_response={response.text}",
                request=exc.request,
                response=exc.response,
            ) from exc
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected dingtalk response: {data}")
        return data

    def send_group_text(self, open_conversation_id: str, text: str) -> dict[str, object]:
        if not self.robot_code:
            raise ValueError("dingtalk client missing robot_code")
        conversation_id = str(open_conversation_id or "").strip()
        if not conversation_id:
            raise ValueError("dingtalk send missing open_conversation_id")

        message = str(text or "").strip()
        if not message:
            raise ValueError("dingtalk send text is empty")

        return self._request(
            "POST",
            "/v1.0/robot/groupMessages/send",
            json_body={
                "msgKey": "sampleText",
                "msgParam": json.dumps({"content": message}, ensure_ascii=False),
                "openConversationId": conversation_id,
                "robotCode": self.robot_code,
            },
        )


@lru_cache(maxsize=8)
def get_dingtalk_bot_client(
    client_id: str,
    client_secret: str,
    robot_code: str,
    api_base_url: str = "https://api.dingtalk.com",
    oauth_url: str = "https://api.dingtalk.com/v1.0/oauth2/accessToken",
    timeout_seconds: int = 10,
) -> DingTalkBotClient:
    return DingTalkBotClient(
        client_id=client_id,
        client_secret=client_secret,
        robot_code=robot_code,
        api_base_url=api_base_url,
        oauth_url=oauth_url,
        timeout_seconds=timeout_seconds,
    )