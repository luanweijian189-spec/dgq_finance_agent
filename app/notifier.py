from __future__ import annotations

import logging
import subprocess
from typing import Iterable

import requests

from .qq_official_bot import get_qq_official_bot_client


logger = logging.getLogger(__name__)


class AlertNotifier:
    def send(self, title: str, content: str) -> None:
        raise NotImplementedError


class StdoutNotifier(AlertNotifier):
    def send(self, title: str, content: str) -> None:
        logger.info("[ALERT] %s | %s", title, content)


class WebhookNotifier(AlertNotifier):
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, title: str, content: str) -> None:
        payload = {"title": title, "content": content, "message": f"{title}\n{content}"}
        response = requests.post(self.webhook_url, json=payload, timeout=5)
        response.raise_for_status()


class QQBotNotifier(AlertNotifier):
    def __init__(
        self,
        base_url: str,
        target_type: str = "group",
        target_id: str = "",
        access_token: str = "",
    ) -> None:
        self.base_url = base_url.strip()
        self.target_type = (target_type or "group").strip().lower()
        self.target_id = str(target_id or "").strip()
        self.access_token = access_token.strip()

    def _build_url(self) -> str:
        action = "/send_private_msg" if self.target_type == "private" else "/send_group_msg"
        url = self.base_url.rstrip("/")
        if url.endswith("/send_private_msg") or url.endswith("/send_group_msg"):
            return url
        return f"{url}{action}"

    def send(self, title: str, content: str) -> None:
        if not self.base_url or not self.target_id:
            raise ValueError("qq bot notifier missing base_url or target_id")

        target_key = "user_id" if self.target_type == "private" else "group_id"
        target_value: int | str = int(self.target_id) if self.target_id.isdigit() else self.target_id
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        response = requests.post(
            self._build_url(),
            json={
                target_key: target_value,
                "message": f"{title}\n{content}",
            },
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()


class QQOfficialBotNotifier(AlertNotifier):
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        target_type: str = "group",
        target_id: str = "",
        api_base_url: str = "https://api.sgroup.qq.com",
        token_url: str = "https://bots.qq.com/app/getAppAccessToken",
        timeout_seconds: int = 10,
    ) -> None:
        self.target_type = (target_type or "group").strip().lower()
        self.target_id = str(target_id or "").strip()
        self.client = get_qq_official_bot_client(
            str(app_id or "").strip(),
            str(app_secret or "").strip(),
            str(api_base_url or "https://api.sgroup.qq.com").strip(),
            str(token_url or "https://bots.qq.com/app/getAppAccessToken").strip(),
            max(int(timeout_seconds or 10), 3),
        )

    def send(self, title: str, content: str) -> None:
        if not self.target_id:
            raise ValueError("qq official bot notifier missing target_id")
        self.client.send_text(self.target_type, self.target_id, f"{title}\n{content}".strip())


class OpenClawNotifier(AlertNotifier):
    def __init__(
        self,
        command: str = "openclaw",
        profile: str = "dev",
        channel: str = "qq",
        recipient: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        self.command = command.strip() or "openclaw"
        self.profile = profile.strip() or "dev"
        self.channel = channel.strip() or "qq"
        self.recipient = recipient.strip()
        self.timeout_seconds = max(int(timeout_seconds), 5)

    def send(self, title: str, content: str) -> None:
        message = f"{title}\n{content}".strip()
        args = [
            self.command,
            f"--{self.profile}",
            "agent",
            "--channel",
            self.channel,
            "--deliver",
            "-m",
            message,
        ]
        if self.recipient:
            args.extend(["--to", self.recipient])

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"openclaw send failed(exit={result.returncode}): {output}")


class CompositeNotifier(AlertNotifier):
    def __init__(self, notifiers: Iterable[AlertNotifier]) -> None:
        self.notifiers = [item for item in notifiers if item is not None]

    def send(self, title: str, content: str) -> None:
        if not self.notifiers:
            return

        sent_count = 0
        last_error: Exception | None = None
        for notifier in self.notifiers:
            try:
                notifier.send(title, content)
                sent_count += 1
            except Exception as exc:  # pragma: no cover - 失败时降级到其他通道
                logger.exception("notifier send failed: %s", notifier.__class__.__name__)
                last_error = exc

        if sent_count == 0 and last_error is not None:
            raise last_error
