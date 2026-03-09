#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://$(hostname -I | awk '{print $1}'):8000}"
TOKEN="$(grep '^CONNECTOR_SHARED_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)"
QQ_ACCOUNT="${QQ_ACCOUNT:-1612085779}"
QQ_GROUP_NAME="${QQ_GROUP_NAME:-finance-robot}"

cat <<EOF
=== OpenClaw QQ 接入参数 ===
QQ 接收账号: ${QQ_ACCOUNT}
测试群名: ${QQ_GROUP_NAME}
后端 webhook: ${PUBLIC_BASE_URL%/}/api/connectors/openclaw/webhook
X-Connector-Token: ${TOKEN}

建议转发字段:
- channel=qq
- message 或 text
- sender_name
- sender_id
- group_name=${QQ_GROUP_NAME}
- group_id

命令测试示例:
curl -X POST ${PUBLIC_BASE_URL%/}/api/connectors/openclaw/webhook \\
  -H 'Content-Type: application/json' \\
  -H 'X-Connector-Token: ${TOKEN}' \\
  -d '{"channel":"qq","text":"/status 002384","sender_name":"群友A","sender_id":"123456","group_name":"${QQ_GROUP_NAME}"}'
EOF
