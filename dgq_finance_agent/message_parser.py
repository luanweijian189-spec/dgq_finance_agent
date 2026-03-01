from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ParsedRecommendation:
    stock_code: str
    recommender_name: str
    recommend_ts: datetime
    extracted_logic: str
    original_message: str


class MessageParser:
    STOCK_PATTERN = re.compile(r"(?:^|\D)((?:60|00|30|68)\d{4})(?:\D|$)")
    INTENT_KEYWORDS = (
        "看好",
        "关注",
        "推荐",
        "逻辑",
        "加仓",
        "买入",
        "目标价",
        "估值",
        "催化",
    )
    LOGIC_HINTS = ("逻辑", "因为", "受益", "催化", "业绩", "增速", "订单", "估值")

    def is_recommendation_intent(self, message: str) -> bool:
        text = (message or "").strip()
        if not text:
            return False
        has_stock_code = bool(self.STOCK_PATTERN.search(text))
        has_keyword = any(keyword in text for keyword in self.INTENT_KEYWORDS)
        return has_stock_code and has_keyword

    def extract_stock_codes(self, message: str) -> list[str]:
        matches = [item.group(1) for item in self.STOCK_PATTERN.finditer(message or "")]
        deduplicated: list[str] = []
        seen: set[str] = set()
        for code in matches:
            if code not in seen:
                deduplicated.append(code)
                seen.add(code)
        return deduplicated

    def extract_logic(self, message: str) -> str:
        text = (message or "").strip()
        if not text:
            return ""

        segments = re.split(r"[。！？!?.；;\n]", text)
        candidates = [segment.strip() for segment in segments if segment.strip()]
        for segment in candidates:
            if any(hint in segment for hint in self.LOGIC_HINTS):
                return segment
        return candidates[0] if candidates else text

    def parse_message(
        self,
        message: str,
        recommender_name: str,
        recommend_ts: datetime | None = None,
    ) -> list[ParsedRecommendation]:
        if not self.is_recommendation_intent(message):
            return []

        stock_codes = self.extract_stock_codes(message)
        extracted_logic = self.extract_logic(message)
        timestamp = recommend_ts or datetime.now()

        return [
            ParsedRecommendation(
                stock_code=code,
                recommender_name=recommender_name,
                recommend_ts=timestamp,
                extracted_logic=extracted_logic,
                original_message=message,
            )
            for code in stock_codes
        ]
