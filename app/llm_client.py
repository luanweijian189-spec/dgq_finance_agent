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
        completions_path: str = "/completions",
        api_mode: str = "auto",
        timeout_seconds: int = 15,
        usage_store: LLMUsageStore | None = None,
        feature_name: str = "generic",
    ) -> None:
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.chat_path = chat_path if chat_path.startswith("/") else f"/{chat_path}"
        self.completions_path = completions_path if completions_path.startswith("/") else f"/{completions_path}"
        self.api_mode = str(api_mode or "auto").strip().lower()
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

        started_at = datetime.utcnow()
        last_error = ""

        model_candidates = [self.model]
        if "/" in self.model:
            stripped = self.model.split("/", 1)[1].strip()
            if stripped and stripped not in model_candidates:
                model_candidates.append(stripped)

        try_order: list[str]
        if self.api_mode == "chat":
            try_order = ["chat"]
        elif self.api_mode in {"completion", "completions"}:
            try_order = ["completion"]
        else:
            try_order = ["chat", "completion"]

        for mode in try_order:
            for model in model_candidates:
                try:
                    data = self._request(messages=messages, temperature=temperature, model=model, mode=mode)
                    content = self._extract_content(data)
                    if content:
                        self._record_usage(data=data, started_at=started_at, success=True, error_message="")
                        return content
                except Exception as exc:
                    last_error = str(exc)
                    continue

        self._record_usage(data=None, started_at=started_at, success=False, error_message=last_error)
        return ""

    def _request(self, *, messages: list[dict[str, str]], temperature: float, model: str, mode: str) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if mode == "completion":
            path = self.completions_path
            payload: dict[str, Any] = {
                "model": model,
                "prompt": self._messages_to_prompt(messages),
                "temperature": temperature,
                "max_tokens": 1024,
            }
        else:
            path = self.chat_path
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }

        if self._is_local_endpoint():
            session = requests.Session()
            session.trust_env = False
            response = session.post(
                f"{self.api_base}{path}",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        else:
            response = requests.post(
                f"{self.api_base}{path}",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected llm response: {type(data)}")
        return data

    @staticmethod
    def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for item in messages or []:
            role = str(item.get("role") or "user").strip() or "user"
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"[{role}] {content}")
        return "\n\n".join(lines).strip()

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else None
        if isinstance(message, dict):
            content = str(message.get("content") or "").strip()
            if content:
                return content
        text = str(first.get("text") or "").strip() if isinstance(first, dict) else ""
        return text

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
