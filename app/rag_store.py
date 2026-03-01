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

    def search(self, stock_code: str, stock_name: str, limit: int = 5) -> list[ResearchNote]:
        rows = self._read_all()
        matches: list[ResearchNote] = []
        for row in reversed(rows):
            text = str(row.get("text") or "")
            if stock_code and stock_code in text:
                matches.append(
                    ResearchNote(
                        ts=datetime.fromisoformat(row.get("ts")),
                        source=str(row.get("source") or ""),
                        recommender_name=str(row.get("recommender_name") or ""),
                        text=text,
                    )
                )
                continue
            if stock_name and stock_name in text:
                matches.append(
                    ResearchNote(
                        ts=datetime.fromisoformat(row.get("ts")),
                        source=str(row.get("source") or ""),
                        recommender_name=str(row.get("recommender_name") or ""),
                        text=text,
                    )
                )

            if len(matches) >= limit:
                break

        return matches[:limit]
