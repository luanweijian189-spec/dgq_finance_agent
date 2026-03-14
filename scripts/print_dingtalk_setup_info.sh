#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

get_env() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true
}

CLIENT_ID="$(get_env DINGTALK_CLIENT_ID)"
CLIENT_SECRET="$(get_env DINGTALK_CLIENT_SECRET)"
AGENT_ID="$(get_env DINGTALK_AGENT_ID)"
ROBOT_CODE="$(get_env DINGTALK_ROBOT_CODE)"
OPEN_CONVERSATION_ID="$(get_env DINGTALK_OPEN_CONVERSATION_ID)"
BACKEND_WEBHOOK_URL="$(get_env DINGTALK_STREAM_BACKEND_WEBHOOK_URL)"

if [[ -z "$BACKEND_WEBHOOK_URL" ]]; then
  BACKEND_WEBHOOK_URL="http://127.0.0.1:8000/api/connectors/dingtalk/webhook"
fi

cat <<EOF
=== 钉钉机器人接入参数 ===
Client ID: ${CLIENT_ID:-<未配置>}
Client Secret: $( [[ -n "$CLIENT_SECRET" ]] && printf '<已配置>' || printf '<未配置>' )
Agent ID: ${AGENT_ID:-<未配置>}
Robot Code: ${ROBOT_CODE:-<未配置>}
主动发消息目标 openConversationId: ${OPEN_CONVERSATION_ID:-<未配置>}
后端 webhook: ${BACKEND_WEBHOOK_URL}

推荐配置：
1. 钉钉开放平台创建企业内部应用机器人
2. 消息接收模式选 Stream 模式
3. 把机器人加入目标群
4. 群里 @机器人 发一条消息，观察 relay 日志里的 conversationId
5. 将 conversationId 写入 DINGTALK_OPEN_CONVERSATION_ID，启用主动通知

启动命令：
- 后端服务：scripts/start_prod_server.sh
- Stream relay：scripts/start_dingtalk_stream_relay.sh
EOF