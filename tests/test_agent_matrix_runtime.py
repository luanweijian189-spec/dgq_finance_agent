from __future__ import annotations

import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock

from app.agent import OpenClawCommandHandler
from app.agent_matrix import AgentMatrixManager, AgentMatrixTaskStore, LocalAgentMatrixApiClient


class AgentMatrixRuntimeTests(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tests/.tmp_agent_matrix")
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.script_path = self.tmp_dir / "fake_loop.sh"
        self.script_path.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "cmd=\"${1:-}\"\n"
            "mkdir -p .copilot-loop\n"
            "if [[ \"$cmd\" == \"init\" ]]; then\n"
            "  printf '# prompt\\n' > .copilot-loop/COPILOT_PROMPT.md\n"
            "  echo '已生成首轮提示：'\"$PWD\"'/.copilot-loop/COPILOT_PROMPT.md'\n"
            "elif [[ \"$cmd\" == \"summary\" ]]; then\n"
            "  echo 'SUMMARY OK'\n"
            "elif [[ \"$cmd\" == \"check\" ]]; then\n"
            "  echo 'CHECK OK'\n"
            "else\n"
            "  echo 'UNKNOWN CMD'\n"
            "fi\n",
            encoding="utf-8",
        )
        self.script_path.chmod(0o755)
        store = AgentMatrixTaskStore(str(self.tmp_dir / "tasks.jsonl"))
        client = LocalAgentMatrixApiClient(
            repo_root=str(self.tmp_dir),
            loop_script=str(self.script_path.resolve()),
            brief_dir=str((self.tmp_dir / "briefs").resolve()),
        )
        self.manager = AgentMatrixManager(
            store=store,
            client=client,
            provider="local",
            workspace=str(self.tmp_dir),
            default_branch="main",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_and_dispatch_task(self) -> None:
        task = self.manager.create_task("接入 agent matrix API")
        self.assertEqual("planned", task.status)

        task = self.manager.dispatch_task(task.task_id)
        self.assertEqual("ready", task.status)
        self.assertTrue(task.brief_file)
        self.assertTrue(Path(task.brief_file).exists())
        self.assertTrue(task.prompt_file)
        self.assertTrue(Path(task.prompt_file).exists())
        self.assertIn("已生成", task.init_output)

    def test_openclaw_command_handler_supports_dev_commands(self) -> None:
        handler = OpenClawCommandHandler(MagicMock(), matrix_manager=self.manager)
        create_result = handler.handle("/dev new 新增开发任务", operator="tester")
        self.assertIn("已创建 agent 任务", create_result)

        task_id = create_result.split("：", 1)[1].splitlines()[0].strip()
        run_result = handler.handle(f"/dev run {task_id}", operator="tester")
        self.assertIn(task_id, run_result)
        self.assertIn("状态：ready", run_result)
