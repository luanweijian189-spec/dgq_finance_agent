#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

read_env_value() {
	local key="$1"
	python3 - "$ENV_FILE" "$key" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
key = sys.argv[2]
if not env_path.exists():
		raise SystemExit(0)

prefix = key + "="
for line in env_path.read_text(encoding="utf-8").splitlines():
		if line.startswith(prefix):
				print(line[len(prefix):])
				raise SystemExit(0)
PY
}

TOKEN="$(read_env_value CONNECTOR_SHARED_TOKEN || true)"
PUBLIC_IP="${PUBLIC_IP:-$(curl -s ifconfig.me || true)}"
RELAY_PORT="${QQ_ONEBOT_RELAY_PORT:-$(read_env_value QQ_ONEBOT_RELAY_PORT || true)}"
RELAY_PORT="${RELAY_PORT:-5701}"
BACKEND_PORT="8000"
QQ_ACCOUNT="${QQ_ACCOUNT:-${GO_CQHTTP_QQ:-$(read_env_value GO_CQHTTP_QQ || true)}}"
QQ_ACCOUNT="${QQ_ACCOUNT:-1612085779}"
QQ_GROUP_NAME="${QQ_GROUP_NAME:-finance-robot}"
GO_CQHTTP_HOME="${GO_CQHTTP_HOME:-$(read_env_value GO_CQHTTP_HOME || true)}"
GO_CQHTTP_HOME="${GO_CQHTTP_HOME:-$ROOT_DIR/runtime/go-cqhttp}"

cat <<EOF
=== go-cqhttp / OneBot 接入建议 ===
推荐桥接器: go-cqhttp
原因: 你的机器是 Linux 轻量服务器，当前没有 Docker，go-cqhttp 更适合 headless 场景。

QQ 登录账号: ${QQ_ACCOUNT}
测试群: ${QQ_GROUP_NAME}

本项目后端 webhook:
- http://127.0.0.1:${BACKEND_PORT}/api/connectors/qq/webhook

建议部署方式:
1. go-cqhttp 监听本机 HTTP API: http://127.0.0.1:5700
2. go-cqhttp 反向上报到本机 relay: http://127.0.0.1:${RELAY_PORT}/webhook
3. relay 再转发到后端，并把 reply_message 回发 QQ

relay 需要的关键配置:
- QQ_ONEBOT_RELAY_BACKEND_WEBHOOK_URL=http://127.0.0.1:${BACKEND_PORT}/api/connectors/qq/webhook
- QQ_ONEBOT_RELAY_SHARED_TOKEN=${TOKEN}
- QQ_ONEBOT_RELAY_ONEBOT_API_BASE_URL=http://127.0.0.1:5700
- QQ_ONEBOT_RELAY_ALLOWED_GROUP_IDS=<登录后填真实 group_id>

本次已经准备好的自动化脚本:
- 安装/生成配置: bash scripts/setup_go_cqhttp.sh
- 启动 go-cqhttp: sudo systemctl start go-cqhttp
- 查看二维码日志: sudo journalctl -u go-cqhttp -f
- 绑定测试群: bash scripts/bind_qq_group_by_name.sh finance-robot

默认工作目录:
- ${GO_CQHTTP_HOME}

公网访问前端:
- http://${PUBLIC_IP}/

下一步建议:
1. 执行 bash scripts/setup_go_cqhttp.sh
2. 我来启动 go-cqhttp，你只负责扫码登录 QQ ${QQ_ACCOUNT}
3. 扫码成功后，我再执行群绑定与联调
EOF
