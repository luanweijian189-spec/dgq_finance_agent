#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WEBHOOK_URL="${WEBHOOK_URL:-${BASE_URL%/}/api/connectors/dingtalk/webhook}"
CONNECTOR_TOKEN="${CONNECTOR_TOKEN:-}"
CONVERSATION_ID="${CONVERSATION_ID:-cid_test_group_1}"
CONVERSATION_TITLE="${CONVERSATION_TITLE:-DGQ钉钉测试群}"
SENDER_NAME="${SENDER_NAME:-钉钉群友A}"
SENDER_ID="${SENDER_ID:-ding_user_1}"

headers=(-H 'Content-Type: application/json')
if [[ -n "$CONNECTOR_TOKEN" ]]; then
  headers+=(-H "X-Connector-Token: $CONNECTOR_TOKEN")
fi

echo "[1/2] 测试普通荐股消息 -> $WEBHOOK_URL"
curl -sS "$WEBHOOK_URL" \
  "${headers[@]}" \
  -d "{
    \"channel\": \"dingtalk\",
    \"source\": \"dingtalk_stream\",
    \"conversationType\": \"2\",
    \"conversationId\": \"$CONVERSATION_ID\",
    \"conversationTitle\": \"$CONVERSATION_TITLE\",
    \"senderNick\": \"$SENDER_NAME\",
    \"senderStaffId\": \"$SENDER_ID\",
    \"text\": {\"content\": \"002436 看好，逻辑是订单增长与景气回暖\"}
  }"
echo

echo "[2/2] 测试命令消息 -> $WEBHOOK_URL"
curl -sS "$WEBHOOK_URL" \
  "${headers[@]}" \
  -d "{
    \"channel\": \"dingtalk\",
    \"source\": \"dingtalk_stream\",
    \"conversationType\": \"2\",
    \"conversationId\": \"$CONVERSATION_ID\",
    \"conversationTitle\": \"$CONVERSATION_TITLE\",
    \"senderNick\": \"$SENDER_NAME\",
    \"senderStaffId\": \"$SENDER_ID\",
    \"text\": {\"content\": \"/status 002436\"}
  }"
echo