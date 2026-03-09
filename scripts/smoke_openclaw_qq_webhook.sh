#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WEBHOOK_URL="${WEBHOOK_URL:-${BASE_URL%/}/api/connectors/openclaw/webhook}"
CONNECTOR_TOKEN="${CONNECTOR_TOKEN:-}"
GROUP_NAME="${GROUP_NAME:-DGQ测试群}"
SENDER_NAME="${SENDER_NAME:-QQ群友A}"
SENDER_ID="${SENDER_ID:-qq_user_1}"

headers=(-H 'Content-Type: application/json')
if [[ -n "$CONNECTOR_TOKEN" ]]; then
  headers+=(-H "X-Connector-Token: $CONNECTOR_TOKEN")
fi

echo "[1/2] 测试普通荐股消息 -> $WEBHOOK_URL"
curl -sS "$WEBHOOK_URL" \
  "${headers[@]}" \
  -d "{
    \"channel\": \"qq\",
    \"message\": \"002436 看好，逻辑是订单增长与景气回暖\",
    \"sender_name\": \"$SENDER_NAME\",
    \"sender_id\": \"$SENDER_ID\",
    \"group_name\": \"$GROUP_NAME\"
  }"
echo

echo "[2/2] 测试命令消息 -> $WEBHOOK_URL"
curl -sS "$WEBHOOK_URL" \
  "${headers[@]}" \
  -d "{
    \"channel\": \"qq\",
    \"text\": \"/who $SENDER_NAME\",
    \"sender_name\": \"$SENDER_NAME\",
    \"sender_id\": \"$SENDER_ID\",
    \"group_name\": \"$GROUP_NAME\"
  }"
echo
