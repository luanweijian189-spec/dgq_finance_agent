#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://$(hostname -I | awk '{print $1}'):8000}"

get_env() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true
}

APP_ID="$(get_env QQ_OFFICIAL_BOT_APP_ID)"
APP_SECRET="$(get_env QQ_OFFICIAL_BOT_APP_SECRET)"
TARGET_TYPE="$(get_env QQ_OFFICIAL_BOT_TARGET_TYPE)"
TARGET_ID="$(get_env QQ_OFFICIAL_BOT_TARGET_ID)"
API_BASE_URL="$(get_env QQ_OFFICIAL_BOT_API_BASE_URL)"

if [[ -z "$TARGET_TYPE" ]]; then
  TARGET_TYPE="group"
fi
if [[ -z "$API_BASE_URL" ]]; then
  API_BASE_URL="https://api.sgroup.qq.com"
fi

CALLBACK_URL="${PUBLIC_BASE_URL%/}/api/connectors/qq/official/webhook"

cat <<EOF
=== QQ 官方 Bot 接入参数 ===
官方回调地址: ${CALLBACK_URL}
AppID: ${APP_ID:-<未配置>}
AppSecret: $( [[ -n "$APP_SECRET" ]] && printf '<已配置>' || printf '<未配置>' )
OpenAPI Base URL: ${API_BASE_URL}
主动通知目标类型: ${TARGET_TYPE}
主动通知目标 ID: ${TARGET_ID:-<未配置>}

开放平台建议配置:
1. 事件回调 URL 填: ${CALLBACK_URL}
2. 开启群 @ 机器人消息事件: GROUP_AT_MESSAGE_CREATE
3. 开启 C2C 消息事件: C2C_MESSAGE_CREATE
4. 如需主动推送，再补充目标 ${TARGET_TYPE} 的 openid / id

本地联调:
- 验签/验证回调冒烟脚本: bash scripts/smoke_qq_official_webhook.sh
- 生产环境服务: deploy/tencent/dgq-finance-agent.service
EOF