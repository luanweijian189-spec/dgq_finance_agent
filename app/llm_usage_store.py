from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class LLMUsageStore:
    def __init__(self, file_path: str = "data/llm_usage.jsonl") -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def append_usage(
        self,
        *,
        feature_name: str,
        model: str,
        api_base: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        success: bool,
        latency_ms: int,
        error_message: str = "",
    ) -> None:
        payload = {
            "ts": datetime.utcnow().isoformat(),
            "feature_name": feature_name,
            "model": model,
            "api_base": api_base,
            "prompt_tokens": max(int(prompt_tokens), 0),
            "completion_tokens": max(int(completion_tokens), 0),
            "total_tokens": max(int(total_tokens), 0),
            "success": bool(success),
            "latency_ms": max(int(latency_ms), 0),
            "error_message": str(error_message or "")[:300],
        }
        with self.file_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def list_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.file_path.exists():
            return []
        lines = self.file_path.read_text(encoding="utf-8").splitlines()
        items: list[dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                items.append(item)
            if len(items) >= limit:
                break
        return items

    def summarize_recent(self, hours: int = 24, limit: int = 500) -> dict[str, Any]:
        cutoff = datetime.utcnow() - timedelta(hours=max(int(hours), 1))
        items = self.list_recent(limit=limit)
        filtered: list[dict[str, Any]] = []
        for item in items:
            try:
                ts = datetime.fromisoformat(str(item.get("ts") or ""))
            except Exception:
                continue
            if ts >= cutoff:
                filtered.append(item)

        total_calls = len(filtered)
        success_calls = sum(1 for item in filtered if item.get("success"))
        total_tokens = sum(int(item.get("total_tokens") or 0) for item in filtered)
        prompt_tokens = sum(int(item.get("prompt_tokens") or 0) for item in filtered)
        completion_tokens = sum(int(item.get("completion_tokens") or 0) for item in filtered)
        avg_latency_ms = int(
            sum(int(item.get("latency_ms") or 0) for item in filtered) / total_calls
        ) if total_calls else 0
        features = Counter(str(item.get("feature_name") or "unknown") for item in filtered)

        return {
            "hours": max(int(hours), 1),
            "total_calls": total_calls,
            "success_calls": success_calls,
            "failed_calls": max(total_calls - success_calls, 0),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "avg_latency_ms": avg_latency_ms,
            "top_features": [{"feature_name": name, "calls": count} for name, count in features.most_common(5)],
        }
