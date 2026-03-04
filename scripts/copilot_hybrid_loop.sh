#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOOP_DIR="${ROOT_DIR}/.copilot-loop"
TASK_FILE="${LOOP_DIR}/task.md"
PROMPT_FILE="${LOOP_DIR}/COPILOT_PROMPT.md"
CHECK_LOG="${LOOP_DIR}/last_check.log"
TEST_CMD_DEFAULT="/Users/weijianluan/luan/finance/dgq_finance_agent/.venv/bin/python -m unittest discover -s tests -v"
TEST_CMD="${TEST_CMD:-$TEST_CMD_DEFAULT}"

mkdir -p "${LOOP_DIR}"

print_usage() {
  cat <<'EOF'
Usage:
  bash scripts/copilot_hybrid_loop.sh init "你的任务描述"
  bash scripts/copilot_hybrid_loop.sh check
  bash scripts/copilot_hybrid_loop.sh summary

Optional env:
  TEST_CMD="自定义测试命令"
EOF
}

git_status_short() {
  git -C "${ROOT_DIR}" status --short || true
}

git_changed_files() {
  git -C "${ROOT_DIR}" diff --name-only || true
}

git_diff_excerpt() {
  git -C "${ROOT_DIR}" --no-pager diff -- . ':(exclude).copilot-loop/*' | sed -n '1,240p' || true
}

render_prompt() {
  local mode="$1"
  local details="$2"
  local changed files status
  status="$(git_status_short)"
  files="$(git_changed_files)"
  changed="$(git_diff_excerpt)"

  cat >"${PROMPT_FILE}" <<EOF
# Copilot 迭代提示（${mode}）

请你在当前 VS Code 工作区直接完成以下任务，并自动修改文件：

## 任务目标
$(cat "${TASK_FILE}")

## 本轮上下文
${details}

## 当前 git 变更文件
${files:-<none>}

## 当前 git status
${status:-<clean>}

## 当前 diff 片段（前240行）
\
${changed:-<none>}

## 执行要求
1. 直接在工作区改代码，不要只给建议。
2. 优先修复根因，保持最小改动。
3. 修改后运行测试命令并根据结果继续迭代：
   ${TEST_CMD}
4. 最后输出：改动文件列表、测试结果、剩余风险。
EOF
}

do_init() {
  local task="${1:-}"
  if [[ -z "${task}" ]]; then
    echo "缺少任务描述。"
    print_usage
    exit 1
  fi
  printf '%s\n' "${task}" >"${TASK_FILE}"

  local details
  details="首轮执行。请基于任务目标开始实现，并在完成后自测。"
  render_prompt "init" "${details}"

  echo "已生成首轮提示：${PROMPT_FILE}"
  echo "下一步：把 ${PROMPT_FILE} 的内容粘贴到 Copilot Chat。"
}

do_check() {
  if [[ ! -f "${TASK_FILE}" ]]; then
    echo "未找到任务文件：${TASK_FILE}，请先执行 init。"
    exit 1
  fi

  echo "运行测试命令：${TEST_CMD}"
  set +e
  bash -lc "cd '${ROOT_DIR}' && ${TEST_CMD}" >"${CHECK_LOG}" 2>&1
  local exit_code=$?
  set -e

  local tail_output
  tail_output="$(tail -n 120 "${CHECK_LOG}" 2>/dev/null || true)"

  if [[ ${exit_code} -eq 0 ]]; then
    render_prompt "pass" "测试已通过。请执行最终代码清理（如需要）并给出总结。"
    echo "✅ 测试通过。已生成收尾提示：${PROMPT_FILE}"
  else
    render_prompt "fail" "测试失败（exit=${exit_code}）。请修复以下错误并再次自测：

${tail_output}
"
    echo "❌ 测试失败。已生成下一轮修复提示：${PROMPT_FILE}"
  fi

  echo "测试日志：${CHECK_LOG}"
}

do_summary() {
  echo "=== 任务 ==="
  if [[ -f "${TASK_FILE}" ]]; then
    cat "${TASK_FILE}"
  else
    echo "<未初始化>"
  fi
  echo
  echo "=== 变更文件 ==="
  git_changed_files
  echo
  echo "=== 最近测试日志（末尾40行）==="
  tail -n 40 "${CHECK_LOG}" 2>/dev/null || echo "<暂无测试日志>"
  echo
  echo "=== 当前提示文件 ==="
  echo "${PROMPT_FILE}"
}

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    init)
      shift
      do_init "${*:-}"
      ;;
    check)
      do_check
      ;;
    summary)
      do_summary
      ;;
    *)
      print_usage
      exit 1
      ;;
  esac
}

main "$@"
