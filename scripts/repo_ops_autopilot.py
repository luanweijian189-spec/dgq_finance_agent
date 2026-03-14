from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.llm_client import LLMApiClient


def _tail(text: str, lines: int = 80) -> str:
    chunks = str(text or "").splitlines()
    if not chunks:
        return ""
    return "\n".join(chunks[-lines:]).strip()


def _csv_to_list(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _extract_json(text: str) -> dict:
    payload = str(text or "").strip()
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?", "", payload, flags=re.IGNORECASE).strip()
        payload = re.sub(r"```$", "", payload).strip()

    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", payload)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_touched_files_from_diff(diff_text: str) -> list[str]:
    files: list[str] = []
    for line in str(diff_text or "").splitlines():
        if line.startswith("+++ b/"):
            value = line[len("+++ b/") :].strip()
            if value and value != "/dev/null":
                files.append(value)
    result: list[str] = []
    seen: set[str] = set()
    for item in files:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _is_path_allowed(path: str, allowed_globs: list[str], blocked_globs: list[str]) -> bool:
    normalized = str(path or "").strip().lstrip("./")
    if not normalized:
        return False
    if any(fnmatch(normalized, pattern) for pattern in blocked_globs):
        return False
    if not allowed_globs:
        return True
    return any(fnmatch(normalized, pattern) for pattern in allowed_globs)


def _run_cmd(repo_root: Path, command: str, timeout: int = 600) -> tuple[int, str]:
    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = _tail((result.stdout or "") + "\n" + (result.stderr or ""), lines=120)
    return result.returncode, output


def _build_prompt(
    *,
    objective: str,
    context: str,
    allowed_globs: list[str],
    blocked_globs: list[str],
    max_files: int,
    last_error: str,
) -> list[dict[str, str]]:
    policy_text = (
        f"允许改动文件模式: {', '.join(allowed_globs) if allowed_globs else '<未限制>'}\n"
        f"禁止改动文件模式: {', '.join(blocked_globs) if blocked_globs else '<未限制>'}\n"
        f"最大改动文件数: {max_files}"
    )
    error_text = last_error.strip() or "<无>"
    return [
        {
            "role": "system",
            "content": (
                "你是资深Python工程师。"
                "请仅输出JSON对象，不要输出Markdown代码块。"
                "JSON格式: {\"rationale\":\"...\",\"diff\":\"...\",\"validation_commands\":[\"...\"]}。"
                "其中diff必须是可被 git apply 应用的 unified diff（以 diff --git 开头）。"
                "只做最小必要改动，不得修改禁用路径。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"目标: {objective}\n"
                f"上下文: {context or '<无>'}\n\n"
                f"策略约束:\n{policy_text}\n\n"
                f"上一次失败原因（如有）:\n{error_text}\n\n"
                "请生成一个可直接应用的最小补丁，并给出1条最关键的验证命令。"
            ),
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="repo-ops local autopilot")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--context", default="")
    parser.add_argument("--max-files", type=int, default=20)
    parser.add_argument("--allowed-globs", default="")
    parser.add_argument("--blocked-globs", default="")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--validate-timeout", type=int, default=600)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    client = LLMApiClient(
        api_base=settings.llm_api_base,
        api_key=settings.llm_api_key,
        model=settings.analysis_model,
        chat_path=settings.llm_api_chat_path,
        completions_path=settings.llm_api_completions_path,
        api_mode=settings.llm_api_mode,
        timeout_seconds=settings.llm_api_timeout_seconds,
        feature_name="repo_ops_autopilot",
    )
    if not client.ready:
        print("LLM 配置未就绪，请检查 LLM_API_BASE / LLM_API_KEY / ANALYSIS_MODEL")
        return 2

    allowed_globs = _csv_to_list(args.allowed_globs)
    blocked_globs = _csv_to_list(args.blocked_globs)
    max_files = max(int(args.max_files or 20), 1)
    max_attempts = max(int(args.max_attempts or 2), 1)

    last_error = ""
    for attempt in range(1, max_attempts + 1):
        print(f"[autopilot] attempt={attempt}/{max_attempts}")
        messages = _build_prompt(
            objective=args.objective,
            context=args.context,
            allowed_globs=allowed_globs,
            blocked_globs=blocked_globs,
            max_files=max_files,
            last_error=last_error,
        )
        reply = client.chat(messages=messages, temperature=0)
        payload = _extract_json(reply)
        if not payload:
            last_error = "LLM 输出不是有效 JSON"
            continue

        diff_text = str(payload.get("diff") or "").strip()
        if not diff_text.startswith("diff --git"):
            last_error = "LLM 未返回 unified diff（缺少 diff --git）"
            continue

        touched_files = _parse_touched_files_from_diff(diff_text)
        if not touched_files:
            last_error = "diff 未包含有效文件"
            continue
        if len(touched_files) > max_files:
            last_error = f"改动文件数超限: {len(touched_files)} > {max_files}"
            continue

        blocked = [
            path
            for path in touched_files
            if not _is_path_allowed(path, allowed_globs=allowed_globs, blocked_globs=blocked_globs)
        ]
        if blocked:
            last_error = f"存在不允许的改动路径: {', '.join(blocked)}"
            continue

        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False, encoding="utf-8") as tmp:
            tmp.write(diff_text)
            diff_path = Path(tmp.name)

        check_code, check_out = _run_cmd(repo_root, f"git apply --check '{diff_path}'", timeout=120)
        if check_code != 0:
            last_error = f"git apply --check 失败:\n{check_out}"
            continue

        apply_code, apply_out = _run_cmd(repo_root, f"git apply '{diff_path}'", timeout=120)
        if apply_code != 0:
            last_error = f"git apply 失败:\n{apply_out}"
            continue

        changed_py_files = [item for item in touched_files if item.endswith(".py")]
        if changed_py_files:
            quoted = " ".join(f"'{item}'" for item in changed_py_files)
            py_code, py_out = _run_cmd(
                repo_root,
                f"'{sys.executable}' -m py_compile {quoted}",
                timeout=min(args.validate_timeout, 180),
            )
            if py_code != 0:
                _run_cmd(repo_root, f"git apply -R '{diff_path}'", timeout=120)
                last_error = f"py_compile 失败:\n{py_out}"
                continue

        commands = payload.get("validation_commands")
        validate_command = ""
        if isinstance(commands, list) and commands:
            candidate = str(commands[0] or "").strip()
            if candidate:
                validate_command = candidate

        if validate_command:
            val_code, val_out = _run_cmd(repo_root, validate_command, timeout=args.validate_timeout)
            if val_code != 0:
                _run_cmd(repo_root, f"git apply -R '{diff_path}'", timeout=120)
                last_error = f"验证命令失败: {validate_command}\n{val_out}"
                continue
            print("[autopilot] validate=ok")
            print(_tail(val_out, lines=40))

        print("[autopilot] apply=ok")
        print(f"[autopilot] changed_files={', '.join(touched_files)}")
        return 0

    print("[autopilot] failed")
    print(_tail(last_error or "未知错误", lines=120))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
