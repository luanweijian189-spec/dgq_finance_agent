from __future__ import annotations

from typing import Any

import requests


class StockAnalysisAgent:
    def __init__(self, model_name: str = "rule", api_key: str = "", api_base: str = "") -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")

    def analyze(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        rag_context: list[str],
    ) -> str:
        if self.model_name != "rule" and self.api_key and self.api_base:
            llm_result = self._call_llm(
                stock_code=stock_code,
                stock_name=stock_name,
                logic=logic,
                score=score,
                pnl_percent=pnl_percent,
                max_drawdown=max_drawdown,
                rag_context=rag_context,
            )
            if llm_result:
                return llm_result

        return self._rule_based_analysis(
            stock_code=stock_code,
            stock_name=stock_name,
            logic=logic,
            score=score,
            pnl_percent=pnl_percent,
            max_drawdown=max_drawdown,
            rag_context=rag_context,
        )

    def _call_llm(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        rag_context: list[str],
    ) -> str:
        prompt = (
            f"你是投研复盘助手。请对股票{stock_code} {stock_name}生成3-5句复盘。"
            f"原始逻辑: {logic}; 当前评分:{score:.1f}; 收益:{pnl_percent:.2f}%; 最大回撤:{max_drawdown:.2f}% 。"
            f"参考资讯:{' | '.join(rag_context[:5]) if rag_context else '无'}。"
            "请输出：1) 逻辑是否验证 2) 风险点 3) 明日跟踪点。"
        )
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "你是严谨的A股复盘分析助手。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        try:
            response = requests.post(
                f"{self.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=12,
            )
            response.raise_for_status()
            data = response.json()
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception:
            return ""

    def _rule_based_analysis(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        rag_context: list[str],
    ) -> str:
        trend = "偏强" if score >= 65 else "中性" if score >= 50 else "偏弱"
        logic_valid = "初步验证" if score >= 60 and pnl_percent >= 0 else "仍待验证"
        risk = "回撤可控" if max_drawdown >= -5 else "回撤偏大需降风险"
        context = "；".join(rag_context[:2]) if rag_context else "暂无新增资讯佐证"
        return (
            f"{stock_code}{stock_name}当前综合趋势{trend}，原始逻辑{logic_valid}。"
            f"收益{pnl_percent:.2f}%、最大回撤{max_drawdown:.2f}%，{risk}。"
            f"资讯参考：{context}。建议下一交易日重点跟踪成交量与逻辑兑现信号。"
        )
