from __future__ import annotations

import re

from .services import FinanceAgentService


class OpenClawCommandHandler:
    def __init__(self, service: FinanceAgentService) -> None:
        self.service = service

    def handle(self, command: str, operator: str = "system") -> str:
        command = (command or "").strip()
        if not command:
            return "请输入指令，例如 /status 600519"

        if command.startswith("/status "):
            stock_code = command.split(maxsplit=1)[1].strip()
            return self.service.get_stock_status(stock_code)

        if command.startswith("/who "):
            name = command.split(maxsplit=1)[1].strip()
            return self.service.get_recommender_status(name)

        if command.startswith("/top "):
            try:
                n = max(1, int(command.split(maxsplit=1)[1]))
            except ValueError:
                return "参数错误，示例：/top 5"
            rows = self.service.list_top_stocks(limit=n, reverse=True)
            if not rows:
                return "暂无可排序的股票评分数据"
            detail = "；".join(f"{stock.stock_code}:{daily.evaluation_score:.1f}" for stock, daily in rows)
            return f"TOP {len(rows)} -> {detail}"

        if command.startswith("/worst "):
            try:
                n = max(1, int(command.split(maxsplit=1)[1]))
            except ValueError:
                return "参数错误，示例：/worst 5"
            rows = self.service.list_top_stocks(limit=n, reverse=False)
            if not rows:
                return "暂无可排序的股票评分数据"
            detail = "；".join(f"{stock.stock_code}:{daily.evaluation_score:.1f}" for stock, daily in rows)
            return f"WORST {len(rows)} -> {detail}"

        if command.startswith("/add "):
            payload = command[len("/add ") :].strip()
            pattern = re.compile(r"^((?:60|00|30|68)\d{4})\s+(.+)\s+by\s+(.+)$", re.IGNORECASE)
            match = pattern.match(payload)
            if not match:
                return "格式错误，示例：/add 600519 业绩拐点明确 by 张三"
            stock_code, logic, recommender_name = (
                match.group(1),
                match.group(2).strip(),
                match.group(3).strip(),
            )
            recommendation = self.service.add_manual_recommendation(stock_code, logic, recommender_name)
            return f"已录入推荐 #{recommendation.id}：{stock_code} by {recommender_name}"

        if command.startswith("/alert on "):
            stock_code = command[len("/alert on ") :].strip()
            if not re.match(r"^(60|00|30|68)\d{4}$", stock_code):
                return "格式错误，示例：/alert on 600519"
            self.service.subscribe_alert(stock_code=stock_code, subscriber=operator)
            return f"已订阅 {stock_code} 异动告警"

        return "不支持的指令。可用：/status /who /top /worst /add /alert on"
