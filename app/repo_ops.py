from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import requests
from pydantic import BaseModel, Field


class RepoOpsPolicy(BaseModel):
    base_branch: str = "main"
    target_branch: str = ""
    require_human_approval: bool = True
    allow_git_write: bool = False
    allow_git_push: bool = False
    allow_shell: bool = False
    max_files: int = 20
    allowed_globs: list[str] = Field(default_factory=list)
    blocked_globs: list[str] = Field(default_factory=list)


class RepoOpsTask(BaseModel):
    task_id: str
    objective: str
    context: str = ""
    operator: str = "system"
    source: str = "api"
    conversation_id: str = ""
    provider: str = "local"
    status: str = "planned"
    workspace: str = ""
    linked_agent_task_id: str = ""
    policy: RepoOpsPolicy = Field(default_factory=RepoOpsPolicy)
    created_at: str
    updated_at: str
    latest_message: str = ""
    plan_output: str = ""
    run_output: str = ""
    summary_output: str = ""
    approval_note: str = ""
    external_run_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepoOpsDispatchResult(BaseModel):
    status: str
    latest_message: str = ""
    plan_output: str = ""
    run_output: str = ""
    summary_output: str = ""
    approval_note: str = ""
    external_run_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepoOpsTaskStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, task: RepoOpsTask) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(task.model_dump(), ensure_ascii=False) + "\n")

    def _load_latest_map(self) -> dict[str, RepoOpsTask]:
        tasks: dict[str, RepoOpsTask] = {}
        if not self.path.exists():
            return tasks
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                task = RepoOpsTask(**json.loads(raw))
                tasks[task.task_id] = task
        return tasks

    def get(self, task_id: str) -> RepoOpsTask | None:
        return self._load_latest_map().get(task_id)

    def list(self, limit: int = 20, status: str = "") -> list[RepoOpsTask]:
        items = list(self._load_latest_map().values())
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[: max(int(limit or 20), 1)]


class RepoOpsApiClient:
    def plan(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        raise NotImplementedError

    def execute(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        raise NotImplementedError

    def summarize(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        raise NotImplementedError

    def approve(self, task: RepoOpsTask, note: str = "") -> RepoOpsDispatchResult:
        raise NotImplementedError


class LocalRepoOpsApiClient(RepoOpsApiClient):
    def __init__(self, repo_root: str) -> None:
        self.repo_root = Path(repo_root).resolve()

    @staticmethod
    def _tail(text: str, lines: int = 80) -> str:
        chunks = str(text or "").splitlines()
        if not chunks:
            return ""
        return "\n".join(chunks[-lines:]).strip()

    def _git(self, *args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["git", *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = self._tail((result.stdout or "") + "\n" + (result.stderr or ""))
        return result.returncode, output

    def plan(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        branch_code, branch_output = self._git("rev-parse", "--abbrev-ref", "HEAD")
        status_code, status_output = self._git("status", "--short")
        diff_code, diff_output = self._git("diff", "--stat")
        lines = [
            f"当前分支：{branch_output or '<未知>'}",
            f"目标分支：{task.policy.target_branch or '<未指定>'}",
            f"基线分支：{task.policy.base_branch or '<未指定>'}",
            f"require_human_approval={task.policy.require_human_approval}",
            f"allow_git_write={task.policy.allow_git_write}",
            f"allow_git_push={task.policy.allow_git_push}",
            "",
            "工作区变更：",
            status_output or "<当前工作区干净>",
            "",
            "diff 统计：",
            diff_output or "<暂无未提交 diff>",
        ]
        ok = branch_code == 0 and status_code == 0 and diff_code == 0
        return RepoOpsDispatchResult(
            status="planned" if ok else "failed",
            latest_message=(
                "已生成 repo-ops 计划，可交给外部 agent API 执行。"
                if ok
                else "本地 repo 检查失败。"
            ),
            plan_output="\n".join(lines).strip(),
            metadata={
                "current_branch": branch_output,
                "workspace_dirty": bool((status_output or "").strip()),
            },
        )

    def execute(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        if task.policy.require_human_approval:
            return RepoOpsDispatchResult(
                status="awaiting_approval",
                latest_message="当前策略要求人工审批后才可执行 repo-ops。",
            )
        if not task.policy.allow_git_write:
            return RepoOpsDispatchResult(
                status="ready",
                latest_message="本地 provider 只做编排占位；待明天接入外部高性能模型 API 后即可真正执行写代码。",
            )
        return RepoOpsDispatchResult(
            status="blocked",
            latest_message="出于安全原因，当前本地 provider 不直接执行 git 写入操作；请切换到 HTTP provider。",
        )

    def summarize(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        status_code, status_output = self._git("status", "--short")
        diff_code, diff_output = self._git("diff", "--stat")
        return RepoOpsDispatchResult(
            status="summary_ready" if status_code == 0 and diff_code == 0 else "failed",
            latest_message="已汇总当前 repo-ops 状态。",
            summary_output=(
                f"工作区变更：\n{status_output or '<当前工作区干净>'}\n\n"
                f"diff 统计：\n{diff_output or '<暂无未提交 diff>'}"
            ).strip(),
        )

    def approve(self, task: RepoOpsTask, note: str = "") -> RepoOpsDispatchResult:
        return RepoOpsDispatchResult(
            status="approved",
            latest_message="已记录人工审批，可继续执行 repo-ops。",
            approval_note=note.strip(),
        )


class HttpRepoOpsApiClient(RepoOpsApiClient):
    def __init__(self, base_url: str, api_key: str = "", timeout_seconds: int = 30) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout_seconds = max(int(timeout_seconds or 30), 5)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _ensure_base_url(self) -> None:
        if not self.base_url:
            raise ValueError("repo ops HTTP API 未配置")

    def _post(self, path: str, payload: dict[str, Any]) -> RepoOpsDispatchResult:
        self._ensure_base_url()
        response = requests.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        if not isinstance(data, dict):
            data = {}
        return RepoOpsDispatchResult(
            status=str(data.get("status") or "submitted"),
            latest_message=str(data.get("message") or data.get("latest_message") or "已提交到 repo-ops API"),
            plan_output=str(data.get("plan_output") or ""),
            run_output=str(data.get("run_output") or ""),
            summary_output=str(data.get("summary_output") or ""),
            approval_note=str(data.get("approval_note") or ""),
            external_run_id=str(data.get("run_id") or data.get("external_run_id") or ""),
            metadata=data,
        )

    @staticmethod
    def _task_payload(task: RepoOpsTask) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "objective": task.objective,
            "context": task.context,
            "operator": task.operator,
            "source": task.source,
            "conversation_id": task.conversation_id,
            "workspace": task.workspace,
            "linked_agent_task_id": task.linked_agent_task_id,
            "policy": task.policy.model_dump(),
            "metadata": task.metadata,
        }

    def plan(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        return self._post("/repo-ops/tasks", self._task_payload(task))

    def execute(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        return self._post(f"/repo-ops/tasks/{task.task_id}/execute", self._task_payload(task))

    def summarize(self, task: RepoOpsTask) -> RepoOpsDispatchResult:
        return self._post(f"/repo-ops/tasks/{task.task_id}/summary", self._task_payload(task))

    def approve(self, task: RepoOpsTask, note: str = "") -> RepoOpsDispatchResult:
        payload = self._task_payload(task)
        payload["note"] = note.strip()
        return self._post(f"/repo-ops/tasks/{task.task_id}/approve", payload)


class RepoOpsManager:
    def __init__(
        self,
        store: RepoOpsTaskStore,
        client: RepoOpsApiClient,
        *,
        provider: str = "local",
        workspace: str = ".",
        default_branch: str = "main",
        default_policy: RepoOpsPolicy | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.provider = str(provider or "local").strip() or "local"
        self.workspace = str(workspace or ".").strip() or "."
        self.default_branch = str(default_branch or "main").strip() or "main"
        self.default_policy = default_policy or RepoOpsPolicy(base_branch=self.default_branch)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def describe_policy(self) -> str:
        policy = self.default_policy
        return (
            f"repo-ops provider={self.provider}\n"
            f"workspace={self.workspace}\n"
            f"base_branch={policy.base_branch}\n"
            f"require_human_approval={policy.require_human_approval}\n"
            f"allow_git_write={policy.allow_git_write}\n"
            f"allow_git_push={policy.allow_git_push}"
        )

    def create_task(
        self,
        objective: str,
        *,
        context: str = "",
        operator: str = "system",
        source: str = "api",
        conversation_id: str = "",
        linked_agent_task_id: str = "",
        target_branch: str = "",
    ) -> RepoOpsTask:
        objective = str(objective or "").strip()
        if not objective:
            raise ValueError("objective is required")
        now = self._now()
        policy = self.default_policy.model_copy(deep=True)
        if target_branch:
            policy.target_branch = str(target_branch).strip()
        task = RepoOpsTask(
            task_id=f"ro-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}",
            objective=objective,
            context=str(context or "").strip(),
            operator=str(operator or "system").strip() or "system",
            source=str(source or "api").strip() or "api",
            conversation_id=str(conversation_id or "").strip(),
            provider=self.provider,
            status="planned",
            workspace=self.workspace,
            linked_agent_task_id=str(linked_agent_task_id or "").strip(),
            policy=policy,
            created_at=now,
            updated_at=now,
            latest_message="已创建 repo-ops 任务，等待规划。",
        )
        self.store.save(task)
        return task

    def _save_update(self, task: RepoOpsTask, **updates: Any) -> RepoOpsTask:
        payload = task.model_dump()
        payload.update(updates)
        payload["updated_at"] = self._now()
        updated = RepoOpsTask(**payload)
        self.store.save(updated)
        return updated

    def list_tasks(self, limit: int = 10, status: str = "") -> list[RepoOpsTask]:
        return self.store.list(limit=limit, status=status)

    def get_task(self, task_id: str) -> RepoOpsTask | None:
        return self.store.get(task_id)

    def plan_task(self, task_id: str) -> RepoOpsTask:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        result = self.client.plan(task)
        return self._save_update(
            task,
            status=result.status,
            latest_message=result.latest_message,
            plan_output=result.plan_output or task.plan_output,
            external_run_id=result.external_run_id or task.external_run_id,
            metadata={**task.metadata, **result.metadata},
        )

    def execute_task(self, task_id: str) -> RepoOpsTask:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        result = self.client.execute(task)
        return self._save_update(
            task,
            status=result.status,
            latest_message=result.latest_message,
            run_output=result.run_output or task.run_output,
            external_run_id=result.external_run_id or task.external_run_id,
            metadata={**task.metadata, **result.metadata},
        )

    def summarize_task(self, task_id: str) -> RepoOpsTask:
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

    def approve_task(self, task_id: str, note: str = "") -> RepoOpsTask:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        result = self.client.approve(task, note=note)
        approved_policy = task.policy.model_copy(deep=True)
        approved_policy.require_human_approval = False
        return self._save_update(
            task,
            status=result.status,
            latest_message=result.latest_message,
            approval_note=result.approval_note or note.strip() or task.approval_note,
            policy=approved_policy,
            external_run_id=result.external_run_id or task.external_run_id,
            metadata={**task.metadata, **result.metadata},
        )

    def format_task_brief(self, task: RepoOpsTask) -> str:
        return (
            f"Repo任务 {task.task_id}\n"
            f"状态：{task.status}\n"
            f"目标：{task.objective}\n"
            f"关联 agent 任务：{task.linked_agent_task_id or '<无>'}\n"
            f"说明：{task.latest_message or '无'}"
        )

    def format_task_list(self, items: Iterable[RepoOpsTask]) -> str:
        rows = list(items)
        if not rows:
            return "当前没有 repo-ops 任务。"
        return "\n".join(f"- {item.task_id} [{item.status}] {item.objective[:48]}" for item in rows)


def _csv_to_list(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def build_repo_ops_manager(settings) -> RepoOpsManager:
    repo_root = Path(__file__).resolve().parents[1]
    provider = str(getattr(settings, "repo_ops_provider", "local") or "local").strip().lower()
    store = RepoOpsTaskStore(str(getattr(settings, "repo_ops_store_path", "data/runtime/repo_ops_tasks.jsonl")))
    if provider == "http" and getattr(settings, "repo_ops_http_base_url", ""):
        client: RepoOpsApiClient = HttpRepoOpsApiClient(
            base_url=settings.repo_ops_http_base_url,
            api_key=settings.repo_ops_http_api_key,
            timeout_seconds=getattr(settings, "repo_ops_timeout_seconds", 30),
        )
    else:
        client = LocalRepoOpsApiClient(repo_root=str(repo_root))
    workspace_value = str(getattr(settings, "repo_ops_workspace", ".") or ".")
    workspace = Path(workspace_value)
    if not workspace.is_absolute():
        workspace = (repo_root / workspace).resolve()
    policy = RepoOpsPolicy(
        base_branch=str(getattr(settings, "repo_ops_default_branch", "main") or "main"),
        require_human_approval=bool(getattr(settings, "repo_ops_require_human_approval", True)),
        allow_git_write=bool(getattr(settings, "repo_ops_allow_git_write", False)),
        allow_git_push=bool(getattr(settings, "repo_ops_allow_git_push", False)),
        allow_shell=bool(getattr(settings, "repo_ops_allow_shell", False)),
        max_files=max(int(getattr(settings, "repo_ops_max_files", 20) or 20), 1),
        allowed_globs=_csv_to_list(getattr(settings, "repo_ops_allowed_globs", "")),
        blocked_globs=_csv_to_list(getattr(settings, "repo_ops_blocked_globs", "")),
    )
    return RepoOpsManager(
        store=store,
        client=client,
        provider=provider,
        workspace=str(workspace),
        default_branch=policy.base_branch,
        default_policy=policy,
    )