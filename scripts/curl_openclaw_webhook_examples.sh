#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TOKEN="${CONNECTOR_TOKEN:-}"
URL="${BASE_URL%/}/api/connectors/openclaw/webhook"

headers=(-H 'Content-Type: application/json')
if [[ -n "$TOKEN" ]]; then
  headers+=(-H "X-Connector-Token: $TOKEN")
fi

echo '[example 1] 普通荐股消息'
curl -sS "$URL" "${headers[@]}" -d '{
  "channel": "qq",
  "message": "002384 看好，逻辑是消费电子复苏",
  "sender_name": "群友A",
  "sender_id": "123456",
  "group_name": "DGQ测试群",
  "group_id": "987654"
}'
echo

echo '[example 2] 命令消息'
curl -sS "$URL" "${headers[@]}" -d '{
  "channel": "qq",
  "text": "/status 002384",
  "sender_name": "群友A",
  "sender_id": "123456",
  "group_name": "DGQ测试群",
  "group_id": "987654"
}'
echo
