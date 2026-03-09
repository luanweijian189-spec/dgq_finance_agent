#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WEBHOOK_URL="${WEBHOOK_URL:-${BASE_URL%/}/api/connectors/qq/official/webhook}"
APP_SECRET="${QQ_OFFICIAL_BOT_APP_SECRET:-}"
APP_ID="${QQ_OFFICIAL_BOT_APP_ID:-}"
EVENT_MODE="${EVENT_MODE:-group}"
COMMAND_TEXT="${COMMAND_TEXT:-/status 002436}"
GROUP_ID="${GROUP_ID:-test_group_openid_1}"
USER_ID="${USER_ID:-test_user_openid_1}"
SENDER_NAME="${SENDER_NAME:-QQ测试用户}"

if [[ -z "$APP_SECRET" && -f "$ENV_FILE" ]]; then
  APP_SECRET="$(grep '^QQ_OFFICIAL_BOT_APP_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
fi
if [[ -z "$APP_ID" && -f "$ENV_FILE" ]]; then
  APP_ID="$(grep '^QQ_OFFICIAL_BOT_APP_ID=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
fi

if [[ -z "$APP_SECRET" ]]; then
  echo "QQ_OFFICIAL_BOT_APP_SECRET 未配置" >&2
  exit 1
fi

python3 - "$WEBHOOK_URL" "$APP_SECRET" "$APP_ID" "$EVENT_MODE" "$COMMAND_TEXT" "$GROUP_ID" "$USER_ID" "$SENDER_NAME" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request
from nacl.signing import SigningKey

webhook_url, app_secret, app_id, event_mode, command_text, group_id, user_id, sender_name = sys.argv[1:9]


def build_seed(secret: str) -> bytes:
    seed = secret
    while len(seed) < 32:
        seed = seed * 2
    return seed[:32].encode("utf-8")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    signing_key = SigningKey(build_seed(secret))
    signed = signing_key.sign(timestamp.encode("utf-8") + body)
    return signed.signature.hex()


def post_json(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Signature-Timestamp": timestamp,
        "X-Signature-Ed25519": sign(app_secret, timestamp, body),
    }
    if app_id:
        headers["X-Union-Appid"] = app_id
    req = urllib.request.Request(webhook_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(resp.status, resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        print(exc.code, response_body)
        raise


event_ts = str(int(time.time()))
print("[1/2] 测试官方校验回调 ->", webhook_url)
post_json({"op": 13, "d": {"plain_token": "dgq_plain_token", "event_ts": event_ts}})

print("[2/2] 测试官方群/C2C命令消息 ->", webhook_url)
if event_mode.lower() == "private":
    payload = {
        "op": 0,
        "s": 1,
        "t": "C2C_MESSAGE_CREATE",
        "id": "evt_c2c_smoke_1",
        "d": {
            "id": "msg_c2c_smoke_1",
            "content": command_text,
            "author": {
                "id": user_id,
                "user_openid": user_id,
                "username": sender_name,
                "bot": False,
            },
        },
    }
else:
    payload = {
        "op": 0,
        "s": 1,
        "t": "GROUP_AT_MESSAGE_CREATE",
        "id": "evt_group_smoke_1",
        "d": {
            "id": "msg_group_smoke_1",
            "group_id": group_id,
            "content": f"<@!bot> {command_text}",
            "author": {
                "id": user_id,
                "user_openid": user_id,
                "username": sender_name,
                "bot": False,
            },
            "member": {
                "nick": sender_name,
            },
        },
    }
post_json(payload)
PY