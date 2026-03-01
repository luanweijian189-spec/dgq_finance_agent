from __future__ import annotations

import logging

import requests


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
        payload = {"title": title, "content": content}
        response = requests.post(self.webhook_url, json=payload, timeout=5)
        response.raise_for_status()
