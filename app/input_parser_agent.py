from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .llm_client import LLMApiClient
from .llm_usage_store import LLMUsageStore


@dataclass
class UnderstoodStock:
    stock_code: str
    stock_name: str


@dataclass
class UnderstoodMessage:
    message_type: str
    logic_summary: str
    confidence: float
    stocks: list[UnderstoodStock]
    raw_text: str


class LLMInputParserAgent:
    VALID_TYPES = {"recommendation", "tracking_update", "research", "macro", "noise"}
    STOCK_CODE_PATTERN = re.compile(r"^(60|00|30|68)\d{4}$")

    def __init__(
        self,
        model_name: str,
        api_key: str,
        api_base: str,
        chat_path: str = "/chat/completions",
        completions_path: str = "/completions",
        api_mode: str = "auto",
        timeout_seconds: int = 15,
        usage_store: LLMUsageStore | None = None,
    ) -> None:
        self.client = LLMApiClient(
            api_base=api_base,
            api_key=api_key,
            model=model_name,
            chat_path=chat_path,
            completions_path=completions_path,
            api_mode=api_mode,
            timeout_seconds=timeout_seconds,
            usage_store=usage_store,
            feature_name="input_parser",
        )

    def understand_message(self, message: str) -> UnderstoodMessage | None:
        text = (message or "").strip()
        if not text:
            return None

        raw = self._call_llm(text)
        if not raw:
            return None

        payload = self._extract_json(raw)
        if not payload:
            return None

        message_type = str(payload.get("message_type") or "noise").strip().lower()
        if message_type not in self.VALID_TYPES:
            message_type = "noise"

        try:
            confidence = float(payload.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        logic_summary = str(payload.get("logic_summary") or "").strip()[:300]
        items = payload.get("stocks")
        stocks: list[UnderstoodStock] = []
        if isinstance(items, list):
            for item in items[:8]:
                if not isinstance(item, dict):
                    continue
                stock_code = str(item.get("stock_code") or "").strip()
                stock_name = str(item.get("stock_name") or "").strip()
                if stock_code and not self.STOCK_CODE_PATTERN.match(stock_code):
                    if not stock_name and re.search(r"[\u4e00-\u9fa5A-Za-z]", stock_code):
                        stock_name = stock_code
                    stock_code = ""
                if not stock_code and not stock_name:
                    continue
                stocks.append(UnderstoodStock(stock_code=stock_code, stock_name=stock_name))

        return UnderstoodMessage(
            message_type=message_type,
            logic_summary=logic_summary,
            confidence=confidence,
            stocks=stocks,
            raw_text=raw[:2000],
        )

    def _call_llm(self, message: str) -> str:
        prompt = (
            "你是股票消息结构化抽取器。"
            "请只输出JSON，不要输出markdown代码块。"
            "字段必须包含：message_type, confidence, logic_summary, stocks。"
            "message_type 只能是 recommendation/tracking_update/research/macro/noise。"
            "stocks 是数组，每项包含 stock_code, stock_name。"
            "如果只有股票名没有代码，stock_code 留空。"
            "如果是宏观或无关噪音，stocks 可以为空数组。"
            "recommendation 指明确推荐/关注/看好；tracking_update 指已有标的的跟踪补充；"
            "research 指研究信息；macro 指宏观/行业普适信息；noise 指无价值消息。"
            "logic_summary 要提炼成一句人能看懂的话。\n"
            f"原始消息: {message}"
        )
        messages = [
            {"role": "system", "content": "你是严谨的股票消息解析器，禁止编造股票代码。"},
            {"role": "user", "content": prompt},
        ]
        return self.client.chat(messages=messages, temperature=0.05)

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        matched = re.search(r"\{[\s\S]*\}", raw)
        if not matched:
            return None
        try:
            data = json.loads(matched.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None