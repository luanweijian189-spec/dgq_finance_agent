from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from .rag_store import ResearchNoteStore
from .stock_knowledge_store import StockKnowledgeStore


@dataclass
class MemorySnippet:
    ts: datetime
    source: str
    text: str


class MemoryRetriever:
    def __init__(
        self,
        research_store: ResearchNoteStore,
        stock_knowledge_store: StockKnowledgeStore,
        default_limit: int = 8,
    ) -> None:
        self.research_store = research_store
        self.stock_knowledge_store = stock_knowledge_store
        self.default_limit = max(int(default_limit), 3)

    def retrieve_for_stock(
        self,
        stock_code: str,
        stock_name: str,
        query_text: str = "",
        limit: int | None = None,
    ) -> list[str]:
        final_limit = max(int(limit or self.default_limit), 1)
        candidate_limit = max(final_limit * 2, 6)

        snippets: list[MemorySnippet] = []
        snippets.extend(self._from_research(stock_code, stock_name, candidate_limit))
        snippets.extend(self._from_stock_files(stock_code, stock_name, candidate_limit))

        filtered = self._filter_by_query(snippets, [stock_code, stock_name, query_text])
        ranked = filtered if filtered else snippets
        ranked.sort(key=lambda item: item.ts, reverse=True)

        outputs: list[str] = []
        seen: set[str] = set()
        for item in ranked:
            key = item.text.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            outputs.append(f"{item.ts.date()} [{item.source}] {item.text[:160]}")
            if len(outputs) >= final_limit:
                break
        return outputs

    def _from_research(self, stock_code: str, stock_name: str, limit: int) -> list[MemorySnippet]:
        rows = self.research_store.search_with_metadata(stock_code=stock_code, stock_name=stock_name, limit=limit)
        return [
            MemorySnippet(
                ts=self._safe_ts(row.get("ts")),
                source=str(row.get("source") or "research"),
                text=str(row.get("text") or "").strip(),
            )
            for row in rows
        ]

    def _from_stock_files(self, stock_code: str, stock_name: str, limit: int) -> list[MemorySnippet]:
        rows = self.stock_knowledge_store.search_entries(stock_code=stock_code, stock_name=stock_name, limit=limit)
        return [
            MemorySnippet(
                ts=self._safe_ts(row.get("ts")),
                source=str(row.get("entry_type") or row.get("source") or "stock_memory"),
                text=str(row.get("content") or "").strip(),
            )
            for row in rows
        ]

    def _filter_by_query(self, snippets: Iterable[MemorySnippet], terms: list[str]) -> list[MemorySnippet]:
        normalized_terms = [term.strip().lower() for term in terms if term and term.strip()]
        if not normalized_terms:
            return list(snippets)

        matched: list[MemorySnippet] = []
        for item in snippets:
            text = item.text.lower()
            if any(term in text for term in normalized_terms):
                matched.append(item)
        return matched

    def _safe_ts(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        text = str(value or "").strip()
        if not text:
            return datetime.min
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return datetime.min
