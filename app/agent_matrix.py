from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import requests
from pydantic import BaseModel, Field


class AgentRoleSpec(BaseModel):
    key: str
    name: str
    responsibility: str
    deliverable: str
    prompt_hint: str


class AgentMatrixTask(BaseModel):
    task_id: str
    objective: str
    context: str = ""
    operator: str = "system"
    source: str = "api"
    conversation_id: str = ""
    provider: str = "local"
    status: str = "planned"
    workspace: str = ""
    branch: str = ""
    created_at: str
    updated_at: str
    matrix: list[AgentRoleSpec] = Field(default_factory=list)
    dispatch_count: int = 0
    latest_message: str = ""
    prompt_file: str = ""
    brief_file: str = ""
    init_output: str = ""
    summary_output: str = ""
    check_output: str = ""
    external_run_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMatrixDispatchResult(BaseModel):
    status: str
    latest_message: str = ""
    prompt_file: str = ""
    brief_file: str = ""
    init_output: str = ""
    summary_output: str = ""
    check_output: str = ""
    external_run_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMatrixTaskStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, task: AgentMatrixTask) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(task.model_dump(), ensure_ascii=False) + "\n")

    def _load_latest_map(self) -> dict[str, AgentMatrixTask]:
        tasks: dict[str, AgentMatrixTask] = {}
        if not self.path.exists():
            return tasks
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                data = json.loads(raw)
                task = AgentMatrixTask(**data)
                tasks[task.task_id] = task
        return tasks

    def get(self, task_id: str) -> AgentMatrixTask | None:
        return self._load_latest_map().get(task_id)

    def list(self, limit: int = 20, status: str = "") -> list[AgentMatrixTask]:
        items = list(self._load_latest_map().values())
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[: max(int(limit or 20), 1)]


class AgentMatrixApiClient:
    def dispatch(self, task: AgentMatrixTask, *, auto_check: bool = False) -> AgentMatrixDispatchResult:
        raise NotImplementedError

    def summarize(self, task: AgentMatrixTask) -> AgentMatrixDispatchResult:
        raise NotImplementedError


class LocalAgentMatrixApiClient(AgentMatrixApiClient):
    def __init__(
        self,
        repo_root: str,
        loop_script: str,
        brief_dir: str,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.loop_script = self._resolve_path(loop_script)
        self.brief_dir = self._resolve_path(brief_dir)
        self.brief_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, value: str) -> Path:
        raw = Path(str(value or "").strip())
        if raw.is_absolute():
            return raw
        return (self.repo_root / raw).resolve()

    @staticmethod
    def _tail(text: str, lines: int = 60) -> str:
        chunks = str(text or "").splitlines()
        if not chunks:
            return ""
        return "\n".join(chunks[-lines:]).strip()

    def _run_loop(self, *args: str) -> tuple[int, str]:
        if not self.loop_script.exists():
            return 127, f"未找到 loop 脚本：{self.loop_script}"
        result = subprocess.run(
            ["bash", str(self.loop_script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=1800,
        )
        output = self._tail((result.stdout or "") + "\n" + (result.stderr or ""), lines=120)
        return result.returncode, output

    def _render_brief(self, task: AgentMatrixTask) -> Path:
        brief_path = self.brief_dir / f"{task.task_id}.md"
        lines = [
            f"# Agent Matrix Task {task.task_id}",
            "",
            f"- 目标：{task.objective}",
            f"- 操作人：{task.operator}",
            f"- 来源：{task.source}",
            f"- 工作区：{task.workspace or str(self.repo_root)}",
            f"- 分支：{task.branch or '<未指定>'}",
            "",
            "## 任务上下文",
            task.context or "<无额外上下文>",
            "",
            "## Agent 矩阵",
        ]
        for idx, role in enumerate(task.matrix, start=1):
            lines.extend(
                [
                    f"### {idx}. {role.name} (`{role.key}`)",
                    f"- 职责：{role.responsibility}",
                    f"- 产物：{role.deliverable}",
                    f"- 提示词：{role.prompt_hint}",
                    "",
                ]
            )
        brief_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return brief_path

    def dispatch(self, task: AgentMatrixTask, *, auto_check: bool = False) -> AgentMatrixDispatchResult:
        brief_path = self._render_brief(task)
        init_code, init_output = self._run_loop("init", task.objective)
        summary_code, summary_output = self._run_loop("summary")
        check_output = ""
        final_status = "ready"
        latest_message = "已生成 agent 矩阵任务，可交给 OpenClaw/Copilot 执行。"
        if init_code != 0:
            final_status = "failed"
            latest_message = "本地 loop 初始化失败。"
        elif auto_check:
            check_code, check_output = self._run_loop("check")
            if check_code != 0:
                final_status = "review_required"
                latest_message = "已生成任务并执行检查，当前仍有失败项待修复。"
            else:
                final_status = "validated"
                latest_message = "已生成任务并完成一次本地检查。"
        elif summary_code != 0:
            latest_message = "任务已生成，但 summary 输出失败。"

        prompt_file = str((self.repo_root / ".copilot-loop" / "COPILOT_PROMPT.md").resolve())
        if not Path(prompt_file).exists():
            prompt_file = ""

        return AgentMatrixDispatchResult(
            status=final_status,
            latest_message=latest_message,
            prompt_file=prompt_file,
            brief_file=str(brief_path),
            init_output=init_output,
            summary_output=summary_output,
            check_output=check_output,
            metadata={
                "init_exit_code": init_code,
                "summary_exit_code": summary_code,
                "auto_check": auto_check,
            },
        )

    def summarize(self, task: AgentMatrixTask) -> AgentMatrixDispatchResult:
        summary_code, summary_output = self._run_loop("summary")
        latest_message = "已汇总当前 agent 任务状态。" if summary_code == 0 else "本地 summary 失败。"
        return AgentMatrixDispatchResult(
            status="summary_ready" if summary_code == 0 else "failed",
            latest_message=latest_message,
            prompt_file=task.prompt_file,
            brief_file=task.brief_file,
            summary_output=summary_output,
            metadata={"summary_exit_code": summary_code},
        )


class HttpAgentMatrixApiClient(AgentMatrixApiClient):
    def __init__(self, base_url: str, api_key: str = "", timeout_seconds: int = 30) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout_seconds = max(int(timeout_seconds or 30), 5)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def dispatch(self, task: AgentMatrixTask, *, auto_check: bool = False) -> AgentMatrixDispatchResult:
        if not self.base_url:
            return AgentMatrixDispatchResult(status="failed", latest_message="未配置 agent matrix HTTP API")
        response = requests.post(
            f"{self.base_url}/tasks",
            headers=self._headers(),
            json={
                "task_id": task.task_id,
                "objective": task.objective,
                "context": task.context,
                "operator": task.operator,
                "source": task.source,
                "conversation_id": task.conversation_id,
                "workspace": task.workspace,
                "branch": task.branch,
                "matrix": [role.model_dump() for role in task.matrix],
                "auto_check": auto_check,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        return AgentMatrixDispatchResult(
            status=str(data.get("status") or "dispatched"),
            latest_message=str(data.get("message") or "已提交到外部 agent matrix API"),
            external_run_id=str(data.get("run_id") or ""),
            metadata=data if isinstance(data, dict) else {},
        )

    def summarize(self, task: AgentMatrixTask) -> AgentMatrixDispatchResult:
        if not self.base_url:
            return AgentMatrixDispatchResult(status="failed", latest_message="未配置 agent matrix HTTP API")
        response = requests.get(
            f"{self.base_url}/tasks/{task.task_id}",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        return AgentMatrixDispatchResult(
            status=str(data.get("status") or "summary_ready"),
            latest_message=str(data.get("message") or "已从外部 agent matrix API 获取摘要"),
            summary_output=json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, dict) else str(data),
            external_run_id=str(data.get("run_id") or ""),
            metadata=data if isinstance(data, dict) else {},
        )


class AgentMatrixManager:
    def __init__(
        self,
        store: AgentMatrixTaskStore,
        client: AgentMatrixApiClient,
        *,
        provider: str = "local",
        workspace: str = ".",
        default_branch: str = "main",
    ) -> None:
        self.store = store
        self.client = client
        self.provider = str(provider or "local").strip() or "local"
        self.workspace = str(workspace or ".").strip() or "."
        self.default_branch = str(default_branch or "main").strip() or "main"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def build_default_matrix() -> list[AgentRoleSpec]:
        return [
            AgentRoleSpec(
                key="coordinator",
                name="任务协调 agent",
                responsibility="拆解需求、控制边界、决定执行顺序与回滚策略。",
                deliverable="结构化任务单、风险与依赖说明。",
                prompt_hint="先确定目标、输入输出、涉及文件与验证方式。",
            ),
            AgentRoleSpec(
                key="planner",
                name="方案规划 agent",
                responsibility="阅读代码库并形成实现计划、接口设计与数据流。",
                deliverable="计划清单、模块改动点、API 设计。",
                prompt_hint="优先复用现有服务、配置与 webhook 链路。",
            ),
            AgentRoleSpec(
                key="coder",
                name="编码执行 agent",
                responsibility="根据计划实施代码修改，保持最小改动。",
                deliverable="代码变更、脚本/配置更新。",
                prompt_hint="优先小步提交，避免破坏现有公共接口。",
            ),
            AgentRoleSpec(
                key="reviewer",
                name="代码评审 agent",
                responsibility="检查安全性、回归风险、错误处理与可维护性。",
                deliverable="review 结论、问题清单、修正建议。",
                prompt_hint="关注配置泄漏、路径硬编码、无鉴权接口。",
            ),
            AgentRoleSpec(
                key="tester",
                name="测试验证 agent",
                responsibility="执行测试/冒烟验证，确认关键链路。",
                deliverable="测试记录、失败日志、验证结果。",
                prompt_hint="至少验证 API、命令和核心文件读写。",
            ),
            AgentRoleSpec(
                key="delivery",
                name="交付运营 agent",
                responsibility="汇总可执行说明，准备给 OpenClaw 或外部 API 使用。",
                deliverable="交付摘要、使用说明、后续替换 API 指南。",
                prompt_hint="输出给人和系统都能直接消费的摘要。",
            ),
        ]

    def describe_matrix(self) -> str:
        rows = [f"{idx}. {role.name}：{role.responsibility}" for idx, role in enumerate(self.build_default_matrix(), start=1)]
        return "Agent 矩阵：\n" + "\n".join(rows)

    def create_task(
        self,
        objective: str,
        *,
        context: str = "",
        operator: str = "system",
        source: str = "api",
        conversation_id: str = "",
        branch: str = "",
    ) -> AgentMatrixTask:
        objective = str(objective or "").strip()
        if not objective:
            raise ValueError("objective is required")
        now = self._now()
        task = AgentMatrixTask(
            task_id=f"am-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}",
            objective=objective,
            context=str(context or "").strip(),
            operator=str(operator or "system").strip() or "system",
            source=str(source or "api").strip() or "api",
            conversation_id=str(conversation_id or "").strip(),
            provider=self.provider,
            status="planned",
            workspace=self.workspace,
            branch=str(branch or self.default_branch or "main").strip() or "main",
            created_at=now,
            updated_at=now,
            matrix=self.build_default_matrix(),
            latest_message="已创建 agent 矩阵任务，等待调度。",
        )
        self.store.save(task)
        return task

    def _save_update(self, task: AgentMatrixTask, **updates: Any) -> AgentMatrixTask:
        payload = task.model_dump()
        payload.update(updates)
        payload["updated_at"] = self._now()
        updated = AgentMatrixTask(**payload)
        self.store.save(updated)
        return updated

    def list_tasks(self, limit: int = 10, status: str = "") -> list[AgentMatrixTask]:
        return self.store.list(limit=limit, status=status)

    def get_task(self, task_id: str) -> AgentMatrixTask | None:
        return self.store.get(task_id)

    def dispatch_task(self, task_id: str, *, auto_check: bool = False) -> AgentMatrixTask:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        result = self.client.dispatch(task, auto_check=auto_check)
        return self._save_update(
            task,
            status=result.status,
            dispatch_count=task.dispatch_count + 1,
            latest_message=result.latest_message,
            prompt_file=result.prompt_file or task.prompt_file,
            brief_file=result.brief_file or task.brief_file,
            init_output=result.init_output or task.init_output,
            summary_output=result.summary_output or task.summary_output,
            check_output=result.check_output or task.check_output,
            external_run_id=result.external_run_id or task.external_run_id,
            metadata={**task.metadata, **result.metadata},
        )

    def summarize_task(self, task_id: str) -> AgentMatrixTask:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        result = self.client.summarize(task)
        return self._save_update(
            task,
            status=result.status if result.status not in {"", "summary_ready"} else task.status,
            latest_message=result.latest_message,
            summary_output=result.summary_output or task.summary_output,
            external_run_id=result.external_run_id or task.external_run_id,
            metadata={**task.metadata, **result.metadata},
        )

    def format_task_brief(self, task: AgentMatrixTask) -> str:
        roles = "、".join(role.name for role in task.matrix)
        return (
            f"任务 {task.task_id}\n"
            f"状态：{task.status}\n"
            f"目标：{task.objective}\n"
            f"角色：{roles}\n"
            f"说明：{task.latest_message or '无'}"
        )

    def format_task_list(self, items: Iterable[AgentMatrixTask]) -> str:
        rows = list(items)
        if not rows:
            return "当前没有 agent 矩阵任务。"
        return "\n".join(
            f"- {item.task_id} [{item.status}] {item.objective[:48]}" for item in rows
        )


def build_agent_matrix_manager(settings) -> AgentMatrixManager:
    repo_root = Path(__file__).resolve().parents[1]
    provider = str(getattr(settings, "agent_matrix_provider", "local") or "local").strip().lower()
    store = AgentMatrixTaskStore(str(getattr(settings, "agent_matrix_store_path", "data/runtime/agent_matrix_tasks.jsonl")))
    if provider == "http" and getattr(settings, "agent_matrix_http_base_url", ""):
        client: AgentMatrixApiClient = HttpAgentMatrixApiClient(
            base_url=settings.agent_matrix_http_base_url,
            api_key=settings.agent_matrix_http_api_key,
            timeout_seconds=getattr(settings, "agent_matrix_timeout_seconds", 30),
        )
    else:
        client = LocalAgentMatrixApiClient(
            repo_root=str(repo_root),
            loop_script=str(getattr(settings, "agent_matrix_loop_script", "scripts/copilot_hybrid_loop.sh")),
            brief_dir=str(getattr(settings, "agent_matrix_brief_dir", "data/runtime/agent_matrix")),
        )
    workspace_value = str(getattr(settings, "agent_matrix_workspace", ".") or ".")
    workspace = Path(workspace_value)
    if not workspace.is_absolute():
        workspace = (repo_root / workspace).resolve()
    return AgentMatrixManager(
        store=store,
        client=client,
        provider=provider,
        workspace=str(workspace),
        default_branch=str(getattr(settings, "agent_matrix_default_branch", "main") or "main"),
    )
