from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class ResearchNote:
    ts: datetime
    source: str
    recommender_name: str
    text: str


class ResearchNoteStore:
    def __init__(self, file_path: str) -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def add_note(self, text: str, source: str, recommender_name: str, ts: Optional[datetime] = None) -> None:
        payload = {
            "ts": (ts or datetime.now()).isoformat(),
            "source": source,
            "recommender_name": recommender_name,
            "text": text.strip(),
        }
        with self.file_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.file_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.file_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def search_with_metadata(self, stock_code: str, stock_name: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._read_all()
        matches: list[dict[str, Any]] = []
        for row in reversed(rows):
            text = str(row.get("text") or "")
            if stock_code and stock_code in text:
                matches.append(row)
                continue
            if stock_name and stock_name in text:
                matches.append(row)

            if len(matches) >= limit:
                break

        return matches[:limit]

    def search(self, stock_code: str, stock_name: str, limit: int = 5) -> list[ResearchNote]:
        matches = self.search_with_metadata(stock_code=stock_code, stock_name=stock_name, limit=limit)
        result: list[ResearchNote] = []
        for row in matches:
            ts_raw = row.get("ts")
            try:
                ts = datetime.fromisoformat(str(ts_raw)) if ts_raw else datetime.min
            except Exception:
                ts = datetime.min
            result.append(
                ResearchNote(
                    ts=ts,
                    source=str(row.get("source") or ""),
                    recommender_name=str(row.get("recommender_name") or ""),
                    text=str(row.get("text") or ""),
                )
            )

        return result[:limit]

    def list_recent(self, limit: int = 10, source_prefix: str = "") -> list[dict[str, Any]]:
        rows = self._read_all()
        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            source = str(row.get("source") or "")
            if source_prefix and not source.startswith(source_prefix):
                continue
            result.append(
                {
                    "ts": str(row.get("ts") or ""),
                    "source": source,
                    "recommender_name": str(row.get("recommender_name") or ""),
                    "text": str(row.get("text") or ""),
                }
            )
            if len(result) >= limit:
                break
        return result
