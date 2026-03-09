#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WEBHOOK_URL="${WEBHOOK_URL:-${BASE_URL%/}/api/connectors/qq/webhook}"
CONNECTOR_TOKEN="${CONNECTOR_TOKEN:-}"
GROUP_ID="${GROUP_ID:-123456789}"
GROUP_NAME="${GROUP_NAME:-finance-robot}"
USER_ID="${USER_ID:-1612085779}"
SENDER_NAME="${SENDER_NAME:-QQ群友A}"

headers=(-H 'Content-Type: application/json')
if [[ -n "$CONNECTOR_TOKEN" ]]; then
  headers+=(-H "X-Connector-Token: $CONNECTOR_TOKEN")
fi

echo "[1/2] OneBot 群消息 -> $WEBHOOK_URL"
curl -sS "$WEBHOOK_URL" \
  "${headers[@]}" \
  -d "{
    \"post_type\": \"message\",
    \"message_type\": \"group\",
    \"raw_message\": \"002384 看好，逻辑是消费电子复苏\",
    \"group_id\": $GROUP_ID,
    \"group_name\": \"$GROUP_NAME\",
    \"user_id\": $USER_ID,
    \"sender\": {\"nickname\": \"$SENDER_NAME\"}
  }"
echo

echo "[2/2] OneBot 命令消息 -> $WEBHOOK_URL"
curl -sS "$WEBHOOK_URL" \
  "${headers[@]}" \
  -d "{
    \"post_type\": \"message\",
    \"message_type\": \"group\",
    \"raw_message\": \"/status 002384\",
    \"group_id\": $GROUP_ID,
    \"group_name\": \"$GROUP_NAME\",
    \"user_id\": $USER_ID,
    \"sender\": {\"nickname\": \"$SENDER_NAME\"}
  }"
echo
