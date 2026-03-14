from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .agent_matrix import AgentMatrixManager
from .repo_ops import RepoOpsManager
from .services import FinanceAgentService


class OpenClawCommandHandler:
    def __init__(
        self,
        service: FinanceAgentService,
        matrix_manager: AgentMatrixManager | None = None,
        repo_ops_manager: RepoOpsManager | None = None,
    ) -> None:
        self.service = service
        self.matrix_manager = matrix_manager
        self.repo_ops_manager = repo_ops_manager
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

        if command.startswith("/dev"):
            if self.matrix_manager is None:
                return "agent矩阵未启用"
            payload = command[len("/dev") :].strip()
            if payload in {"", "help"}:
                return (
                    "可用命令：/dev new <任务>；/dev run <task_id>；/dev status [task_id]；"
                    "/dev summary <task_id>；/dev matrix"
                )
            if payload == "matrix":
                return self.matrix_manager.describe_matrix()
            if payload.startswith("new "):
                objective = payload[len("new ") :].strip()
                if not objective:
                    return "格式错误，示例：/dev new 接入钉钉审批回调"
                task = self.matrix_manager.create_task(
                    objective,
                    operator=operator,
                    source="connector_command",
                )
                return (
                    f"已创建 agent 任务：{task.task_id}\n"
                    f"目标：{task.objective}\n"
                    "下一步：/dev run "
                    f"{task.task_id}"
                )
            if payload == "status":
                return self.matrix_manager.format_task_list(self.matrix_manager.list_tasks(limit=5))
            if payload.startswith("status "):
                task_id = payload[len("status ") :].strip()
                task = self.matrix_manager.get_task(task_id)
                if task is None:
                    return f"未找到任务：{task_id}"
                return self.matrix_manager.format_task_brief(task)
            if payload.startswith("run "):
                task_id = payload[len("run ") :].strip()
                try:
                    task = self.matrix_manager.dispatch_task(task_id)
                except ValueError:
                    return f"未找到任务：{task_id}"
                return self.matrix_manager.format_task_brief(task)
            if payload.startswith("summary "):
                task_id = payload[len("summary ") :].strip()
                try:
                    task = self.matrix_manager.summarize_task(task_id)
                except ValueError:
                    return f"未找到任务：{task_id}"
                return self.matrix_manager.format_task_brief(task)
            return "不支持的dev子命令。可用：new/run/status/summary/matrix"

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

        if command.startswith("/repo"):
            if self.repo_ops_manager is None:
                return "repo-ops 未启用"
            payload = command[len("/repo") :].strip()
            if payload in {"", "help"}:
                return (
                    "可用命令：/repo new <任务>；/repo auto <任务>；/repo plan <task_id>；"
                    "/repo run <task_id>；/repo approve <task_id> [备注]；/repo summary <task_id>；"
                    "/repo status [task_id]；/repo policy"
                )
            if payload == "policy":
                return self.repo_ops_manager.describe_policy()
            if payload == "status":
                return self.repo_ops_manager.format_task_list(self.repo_ops_manager.list_tasks(limit=5))
            if payload.startswith("status "):
                task_id = payload[len("status ") :].strip()
                task = self.repo_ops_manager.get_task(task_id)
                if task is None:
                    return f"未找到 repo 任务：{task_id}"
                return self.repo_ops_manager.format_task_brief(task)
            if payload.startswith("new "):
                objective = payload[len("new ") :].strip()
                if not objective:
                    return "格式错误，示例：/repo new 给回测模块增加策略对比接口"
                task = self.repo_ops_manager.create_task(
                    objective,
                    operator=operator,
                    source="connector_command",
                )
                return (
                    f"已创建 repo 任务：{task.task_id}\n"
                    f"目标：{task.objective}\n"
                    f"下一步：/repo plan {task.task_id}"
                )
            if payload.startswith("auto "):
                objective = payload[len("auto ") :].strip()
                if not objective:
                    return "格式错误，示例：/repo auto 给 dashboard 增加昨收对比"
                agent_task_id = ""
                if self.matrix_manager is not None:
                    agent_task = self.matrix_manager.create_task(
                        objective,
                        operator=operator,
                        source="repo_autopilot",
                    )
                    agent_task = self.matrix_manager.dispatch_task(agent_task.task_id)
                    agent_task_id = agent_task.task_id
                repo_task = self.repo_ops_manager.create_task(
                    objective,
                    operator=operator,
                    source="repo_autopilot",
                    linked_agent_task_id=agent_task_id,
                )
                repo_task = self.repo_ops_manager.plan_task(repo_task.task_id)
                return (
                    f"已启动 autopilot。\n"
                    f"agent任务：{agent_task_id or '<未创建>'}\n"
                    f"repo任务：{repo_task.task_id}\n"
                    f"状态：{repo_task.status}\n"
                    f"说明：{repo_task.latest_message}"
                )
            if payload.startswith("plan "):
                task_id = payload[len("plan ") :].strip()
                try:
                    task = self.repo_ops_manager.plan_task(task_id)
                except ValueError:
                    return f"未找到 repo 任务：{task_id}"
                return self.repo_ops_manager.format_task_brief(task)
            if payload.startswith("run "):
                task_id = payload[len("run ") :].strip()
                try:
                    task = self.repo_ops_manager.execute_task(task_id)
                except ValueError:
                    return f"未找到 repo 任务：{task_id}"
                return self.repo_ops_manager.format_task_brief(task)
            if payload.startswith("summary "):
                task_id = payload[len("summary ") :].strip()
                try:
                    task = self.repo_ops_manager.summarize_task(task_id)
                except ValueError:
                    return f"未找到 repo 任务：{task_id}"
                return self.repo_ops_manager.format_task_brief(task)
            if payload.startswith("approve "):
                rest = payload[len("approve ") :].strip()
                task_id, _, note = rest.partition(" ")
                try:
                    task = self.repo_ops_manager.approve_task(task_id.strip(), note.strip())
                except ValueError:
                    return f"未找到 repo 任务：{task_id.strip()}"
                return self.repo_ops_manager.format_task_brief(task)
            return "不支持的repo子命令。可用：new/auto/plan/run/approve/summary/status/policy"

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
