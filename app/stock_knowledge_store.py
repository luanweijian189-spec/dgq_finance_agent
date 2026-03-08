from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class StockKnowledgeStore:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _stock_file(self, stock_code: str, stock_name: str = "") -> Path:
        code = (stock_code or "UNKNOWN").replace("/", "_")
        name = (stock_name or "").replace("/", "_").replace(" ", "")
        filename = f"{code}.jsonl" if not name else f"{code}_{name}.jsonl"
        return self.base_dir / filename

    def append_entry(
        self,
        stock_code: str,
        stock_name: str,
        source: str,
        operator: str,
        entry_type: str,
        content: str,
        ts: Optional[datetime] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "ts": (ts or datetime.now()).isoformat(),
            "stock_code": stock_code,
            "stock_name": stock_name,
            "source": source,
            "operator": operator,
            "entry_type": entry_type,
            "content": (content or "").strip(),
        }
        file_path = self._stock_file(stock_code, stock_name)
        with file_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def search_entries(self, stock_code: str, stock_name: str = "", limit: int = 6) -> list[dict[str, Any]]:
        candidates = list(self.base_dir.glob(f"{stock_code}*.jsonl"))
        if not candidates and stock_name:
            candidates = list(self.base_dir.glob(f"*{stock_name}*.jsonl"))
        if not candidates:
            return []

        merged_lines: list[str] = []
        for file_path in candidates:
            try:
                with file_path.open("r", encoding="utf-8") as fp:
                    for line in fp:
                        line = line.strip()
                        if line:
                            merged_lines.append(line)
            except Exception:
                continue

        result: list[dict[str, Any]] = []
        for line in reversed(merged_lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            result.append(
                {
                    "ts": row.get("ts", ""),
                    "entry_type": row.get("entry_type", ""),
                    "source": row.get("source", ""),
                    "content": row.get("content", ""),
                }
            )
            if len(result) >= limit:
                break
        return result

    def search(self, stock_code: str, stock_name: str = "", limit: int = 6) -> list[str]:
        rows = self.search_entries(stock_code=stock_code, stock_name=stock_name, limit=limit)
        return [f"{row.get('ts', '')} {row.get('entry_type', '')} {row.get('content', '')}" for row in rows]

    def list_recent_entries(
        self,
        limit: int = 10,
        entry_types: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for file_path in self.base_dir.glob("*.jsonl"):
            try:
                with file_path.open("r", encoding="utf-8") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        entry_type = str(payload.get("entry_type") or "")
                        if entry_types and entry_type not in entry_types:
                            continue
                        rows.append(
                            {
                                "ts": str(payload.get("ts") or ""),
                                "stock_code": str(payload.get("stock_code") or ""),
                                "stock_name": str(payload.get("stock_name") or ""),
                                "source": str(payload.get("source") or ""),
                                "operator": str(payload.get("operator") or ""),
                                "entry_type": entry_type,
                                "content": str(payload.get("content") or ""),
                            }
                        )
            except Exception:
                continue

        rows.sort(key=lambda item: item.get("ts", ""), reverse=True)
        return rows[:limit]
