from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .services import FinanceAgentService


class OpenClawCommandHandler:
    def __init__(self, service: FinanceAgentService) -> None:
        self.service = service
        self._repo_root = Path(__file__).resolve().parents[1]
        self._loop_script = self._repo_root / "scripts" / "copilot_hybrid_loop.sh"

    @staticmethod
    def _tail(text: str, lines: int = 40) -> str:
        chunks = (text or "").strip().splitlines()
        if not chunks:
            return ""
        return "\n".join(chunks[-lines:])

    def _run_loop(self, action: str, *args: str) -> str:
        if not self._loop_script.exists():
            return f"未找到脚本：{self._loop_script}"

        result = subprocess.run(
            ["bash", str(self._loop_script), action, *args],
            cwd=str(self._repo_root),
            capture_output=True,
            text=True,
            timeout=900,
        )
        output = self._tail(result.stdout or result.stderr, lines=80)
        if result.returncode != 0:
            return f"loop命令执行失败（exit={result.returncode}）\n{output}"
        return output or "执行完成"

    def handle(self, command: str, operator: str = "system") -> str:
        command = (command or "").strip()
        if not command:
            return "请输入指令，例如 /status 600519"

        if command.startswith("/loop "):
            payload = command[len("/loop ") :].strip()
            if payload.startswith("init "):
                task = payload[len("init ") :].strip()
                if not task:
                    return "格式错误，示例：/loop init 修复某个功能并自测"
                return self._run_loop("init", task)
            if payload == "check":
                return self._run_loop("check")
            if payload in {"summary", "status"}:
                return self._run_loop("summary")
            return "不支持的loop子命令。可用：/loop init <任务> /loop check /loop summary"

        if command.startswith("/discover"):
            payload = command[len("/discover") :].strip()
            if payload in {"", "list"}:
                rows = self.service.list_news_candidates(limit=5, status="candidate")
                if not rows:
                    return "暂无候选新股"
                detail = "；".join(
                    f"#{row['id']} {row['stock_code']} {row.get('stock_name','')}({row['discovery_score']:.2f})"
                    for row in rows
                )
                return f"候选新股: {detail}"
            if payload == "scan":
                result = self.service.run_news_discovery_scan()
                return (
                    f"扫描完成: 原始{result['raw_discovered']}条，保存{result['saved_candidates']}条，"
                    f"更新存量{result['updated_tracking']}条，自动晋升{result['promoted']}条"
                )
            if payload.startswith("promote "):
                raw_id = payload[len("promote ") :].strip()
                if not raw_id.isdigit():
                    return "格式错误，示例：/discover promote 12"
                recommendation = self.service.promote_news_candidate(int(raw_id), operator=operator)
                if recommendation is None:
                    return "候选不存在"
                return f"已晋升到跟踪池：#{recommendation.id} {recommendation.stock.stock_code}"
            return "不支持的discover子命令。可用：/discover scan|list|promote <id>"

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

        return "不支持的指令。可用：/status /who /top /worst /add /alert on /loop /discover"
