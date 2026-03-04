from __future__ import annotations

from typing import Any

import requests


class LLMApiClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        chat_path: str = "/chat/completions",
        timeout_seconds: int = 15,
    ) -> None:
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.chat_path = chat_path if chat_path.startswith("/") else f"/{chat_path}"
        self.timeout_seconds = max(int(timeout_seconds), 3)

    @property
    def ready(self) -> bool:
        return bool(self.api_base and self.api_key and self.model)

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        if not self.ready:
            return ""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        try:
            response = requests.post(
                f"{self.api_base}{self.chat_path}",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception:
            return ""
