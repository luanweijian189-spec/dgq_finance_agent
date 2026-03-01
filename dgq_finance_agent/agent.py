from __future__ import annotations

import re

from .service import FinanceResearchService


class AgentCommandHandler:
    def __init__(self, service: FinanceResearchService) -> None:
        self.service = service

    def handle(self, command: str) -> str:
        command = (command or "").strip()
        if not command:
            return "请输入指令，例如 /status 600519"

        if command.startswith("/status "):
            stock_code = command.split(maxsplit=1)[1].strip()
            return self._status(stock_code)

        if command.startswith("/who "):
            name = command.split(maxsplit=1)[1].strip()
            return self._who(name)

        if command.startswith("/top "):
            value = command.split(maxsplit=1)[1].strip()
            return self._top(value)

        if command.startswith("/worst "):
            value = command.split(maxsplit=1)[1].strip()
            return self._worst(value)

        if command.startswith("/add "):
            payload = command[len("/add ") :].strip()
            return self._add(payload)

        return "不支持的指令。可用：/status /who /top /worst /add"

    def _status(self, stock_code: str) -> str:
        stock = self.service.repository.get_stock_by_code(stock_code)
        if stock is None:
            return f"未找到股票 {stock_code}"

        latest = self.service.repository.latest_daily_for_stock(stock_code)
        if latest is None:
            return f"{stock_code} 已在池中，但暂无日评估数据"

        return (
            f"{stock_code} 最新评分 {latest.evaluation_score:.1f}，"
            f"当前收益 {latest.pnl_percent:.2f}% ，"
            f"最大回撤 {latest.max_drawdown:.2f}% 。"
        )

    def _who(self, name: str) -> str:
        recommender = self.service.repository.get_recommender_by_name(name)
        if recommender is None:
            return f"未找到荐股人 {name}"

        recs = self.service.repository.get_recommendations_for_recommender(recommender.id)
        return (
            f"{recommender.name} 可靠性评分 {recommender.reliability_score:.1f}，"
            f"历史推荐 {len(recs)} 条。"
        )

    def _top(self, value: str) -> str:
        try:
            n = max(1, int(value))
        except ValueError:
            return "参数错误，示例：/top 5"

        rows = self.service.repository.get_top_stocks(n=n, reverse=True)
        if not rows:
            return "暂无可排序的股票评分数据"

        detail = "；".join(f"{stock.stock_code}:{daily.evaluation_score:.1f}" for stock, daily in rows)
        return f"TOP {len(rows)} -> {detail}"

    def _worst(self, value: str) -> str:
        try:
            n = max(1, int(value))
        except ValueError:
            return "参数错误，示例：/worst 5"

        rows = self.service.repository.get_top_stocks(n=n, reverse=False)
        if not rows:
            return "暂无可排序的股票评分数据"

        detail = "；".join(f"{stock.stock_code}:{daily.evaluation_score:.1f}" for stock, daily in rows)
        return f"WORST {len(rows)} -> {detail}"

    def _add(self, payload: str) -> str:
        pattern = re.compile(r"^((?:60|00|30|68)\d{4})\s+(.+)\s+by\s+(.+)$", re.IGNORECASE)
        match = pattern.match(payload)
        if not match:
            return "格式错误，示例：/add 600519 业绩拐点明确 by 张三"

        stock_code, logic, recommender_name = match.group(1), match.group(2).strip(), match.group(3).strip()
        recommendation = self.service.add_manual_recommendation(
            stock_code=stock_code,
            logic=logic,
            recommender_name=recommender_name,
        )
        return f"已录入推荐 #{recommendation.id}：{stock_code} by {recommender_name}"
