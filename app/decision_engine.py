from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .llm_client import LLMApiClient
from .llm_usage_store import LLMUsageStore


@dataclass
class AIDecision:
    direction: str
    confidence: float
    horizon_days: int
    thesis: str
    invalidation_conditions: str
    risk_flags: list[str]
    evidence: list[str]
    raw_text: str


class LLMDecisionEngine:
    def __init__(
        self,
        model_name: str,
        api_key: str,
        api_base: str,
        chat_path: str = "/chat/completions",
        timeout_seconds: int = 20,
        usage_store: LLMUsageStore | None = None,
    ) -> None:
        self.client = LLMApiClient(
            api_base=api_base,
            api_key=api_key,
            model=model_name,
            chat_path=chat_path,
            timeout_seconds=timeout_seconds,
            usage_store=usage_store,
            feature_name="decision_engine",
        )

    def decide(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        memory_context: list[str],
        evidence_bundle: dict[str, Any] | None = None,
    ) -> AIDecision:
        llm_decision = self._decide_by_llm(
            stock_code=stock_code,
            stock_name=stock_name,
            logic=logic,
            score=score,
            pnl_percent=pnl_percent,
            max_drawdown=max_drawdown,
            memory_context=memory_context,
            evidence_bundle=evidence_bundle,
        )
        if llm_decision:
            return llm_decision

        return AIDecision(
            direction="sideways",
            confidence=0.35,
            horizon_days=1,
            thesis="LLM不可用，当前仅输出低置信中性判断。",
            invalidation_conditions="任一关键风险事件新增或开盘后量价显著背离。",
            risk_flags=["llm_unavailable"],
            evidence=memory_context[:3],
            raw_text="fallback",
        )

    def _decide_by_llm(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        memory_context: list[str],
        evidence_bundle: dict[str, Any] | None,
    ) -> AIDecision | None:
        evidence_text = json.dumps(evidence_bundle or {}, ensure_ascii=False)[:2600]
        prompt = (
            "你是A股交易决策引擎，不要写空话。"
            "请只输出JSON，不要输出markdown代码块。"
            "字段必须包含：direction, confidence, horizon_days, thesis, invalidation_conditions, risk_flags, evidence。"
            "direction只能是 up/down/sideways；confidence取值0到1。"
            "若证据不足，direction=sideways 且 confidence<=0.45。\n"
            f"股票: {stock_code} {stock_name}\n"
            f"原始逻辑: {logic}\n"
            f"参考风险分数(SQS): {score:.1f}\n"
            f"当前收益: {pnl_percent:.2f}%\n"
            f"最大回撤: {max_drawdown:.2f}%\n"
            f"记忆证据: {' | '.join(memory_context[:8]) if memory_context else '无'}\n"
            f"结构化证据包: {evidence_text if evidence_text and evidence_text != '{}' else '无'}"
        )
        messages = [
            {"role": "system", "content": "你是严谨的A股交易分析员，结论必须可证伪。"},
            {"role": "user", "content": prompt},
        ]
        text = self.client.chat(messages=messages, temperature=0.05)
        if not text:
            return None
        payload = self._extract_json(text)
        if payload is None:
            return None

        direction = str(payload.get("direction") or "sideways").strip().lower()
        if direction not in {"up", "down", "sideways"}:
            direction = "sideways"
        try:
            confidence = float(payload.get("confidence", 0.35))
        except Exception:
            confidence = 0.35
        confidence = max(0.0, min(1.0, confidence))
        try:
            horizon_days = int(payload.get("horizon_days", 1))
        except Exception:
            horizon_days = 1
        horizon_days = max(1, min(10, horizon_days))

        risk_flags = payload.get("risk_flags")
        evidence = payload.get("evidence")
        if not isinstance(risk_flags, list):
            risk_flags = []
        if not isinstance(evidence, list):
            evidence = []

        return AIDecision(
            direction=direction,
            confidence=confidence,
            horizon_days=horizon_days,
            thesis=str(payload.get("thesis") or "").strip()[:400],
            invalidation_conditions=str(payload.get("invalidation_conditions") or "").strip()[:400],
            risk_flags=[str(item)[:60] for item in risk_flags[:8]],
            evidence=[str(item)[:160] for item in evidence[:8]],
            raw_text=text[:2000],
        )

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
