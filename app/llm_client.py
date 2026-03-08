from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from .llm_usage_store import LLMUsageStore


class LLMApiClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        chat_path: str = "/chat/completions",
        timeout_seconds: int = 15,
        usage_store: LLMUsageStore | None = None,
        feature_name: str = "generic",
    ) -> None:
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.chat_path = chat_path if chat_path.startswith("/") else f"/{chat_path}"
        self.timeout_seconds = max(int(timeout_seconds), 3)
        self.usage_store = usage_store
        self.feature_name = feature_name or "generic"

    @property
    def ready(self) -> bool:
        return bool(self.api_base and self.model and (self.api_key or self._is_local_endpoint()))

    def _is_local_endpoint(self) -> bool:
        return self.api_base.startswith("http://127.0.0.1") or self.api_base.startswith("http://localhost")

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        if not self.ready:
            return ""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        started_at = datetime.utcnow()

        try:
            headers = {
                "Content-Type": "application/json",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            if self._is_local_endpoint():
                session = requests.Session()
                session.trust_env = False
                response = session.post(
                    f"{self.api_base}{self.chat_path}",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            else:
                response = requests.post(
                    f"{self.api_base}{self.chat_path}",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            data = response.json()
            self._record_usage(data=data, started_at=started_at, success=True, error_message="")
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception as exc:
            self._record_usage(data=None, started_at=started_at, success=False, error_message=str(exc))
            return ""

    def _record_usage(
        self,
        *,
        data: dict[str, Any] | None,
        started_at: datetime,
        success: bool,
        error_message: str,
    ) -> None:
        if self.usage_store is None:
            return
        prompt_tokens, completion_tokens, total_tokens = self._extract_usage(data or {})
        latency_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)
        self.usage_store.append_usage(
            feature_name=self.feature_name,
            model=self.model,
            api_base=self.api_base,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            success=success,
            latency_ms=latency_ms,
            error_message=error_message,
        )

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[int, int, int]:
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            return prompt_tokens, completion_tokens, total_tokens

        prompt_tokens = int(data.get("prompt_eval_count") or data.get("prompt_tokens") or 0)
        completion_tokens = int(data.get("eval_count") or data.get("completion_tokens") or 0)
        total_tokens = int(data.get("total_tokens") or (prompt_tokens + completion_tokens))
        return prompt_tokens, completion_tokens, total_tokens
