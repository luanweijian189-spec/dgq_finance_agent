from __future__ import annotations

import json
from typing import Any

from .llm_client import LLMApiClient
from .llm_usage_store import LLMUsageStore


class StockAnalysisAgent:
    def __init__(
        self,
        model_name: str = "gpt-5.3-codex",
        api_key: str = "",
        api_base: str = "",
        chat_path: str = "/chat/completions",
        completions_path: str = "/completions",
        api_mode: str = "auto",
        timeout_seconds: int = 15,
        usage_store: LLMUsageStore | None = None,
    ) -> None:
        self.model_name = model_name
        self.client = LLMApiClient(
            api_base=api_base,
            api_key=api_key,
            model=model_name,
            chat_path=chat_path,
            completions_path=completions_path,
            api_mode=api_mode,
            timeout_seconds=timeout_seconds,
            usage_store=usage_store,
            feature_name="analysis_agent",
        )

    def analyze(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        rag_context: list[str],
        evidence_bundle: dict[str, Any] | None = None,
    ) -> str:
        llm_result = self._call_llm(
            stock_code=stock_code,
            stock_name=stock_name,
            logic=logic,
            score=score,
            pnl_percent=pnl_percent,
            max_drawdown=max_drawdown,
            rag_context=rag_context,
            evidence_bundle=evidence_bundle,
        )
        if llm_result:
            return llm_result

        return self._factual_fallback_summary(
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
        evidence_bundle: dict[str, Any] | None,
    ) -> str:
        evidence_text = json.dumps(evidence_bundle or {}, ensure_ascii=False, default=str)[:2400]
        prompt = (
            "你是严谨的A股复盘分析助手。"
            "请基于输入证据输出不超过5句的结论，必须包含："
            "1) 逻辑是否仍成立；2) 主要风险点；3) 下一交易日跟踪项；"
            "4) 触发失效条件。"
            "若证据不足，明确输出“信息不足”。\n"
            f"股票: {stock_code} {stock_name}\n"
            f"原始逻辑: {logic}\n"
            f"当日评分: {score:.1f}\n"
            f"收益: {pnl_percent:.2f}%\n"
            f"最大回撤: {max_drawdown:.2f}%\n"
            f"记忆上下文: {' | '.join(rag_context[:8]) if rag_context else '无'}\n"
            f"结构化证据包: {evidence_text if evidence_text and evidence_text != '{}' else '无'}"
        )
        messages = [
            {"role": "system", "content": "你是严谨且保守的A股分析助手，禁止夸大确定性。"},
            {"role": "user", "content": prompt},
        ]
        return self.client.chat(messages=messages, temperature=0.1)

    def _factual_fallback_summary(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        rag_context: list[str],
    ) -> str:
        context = "；".join(rag_context[:2]) if rag_context else "暂无可用记忆上下文"
        return (
            f"{stock_code}{stock_name}复盘摘要：原始逻辑“{logic}”。"
            f"当日评分{score:.1f}，收益{pnl_percent:.2f}%，最大回撤{max_drawdown:.2f}%。"
            f"记忆参考：{context}。"
            "当前未获取到可用LLM响应，建议检查LLM API配置后重试智能分析。"
        )
