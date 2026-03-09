#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
TARGET_GROUP_NAME="${1:-finance-robot}"

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

ONEBOT_API_BASE_URL="${QQ_ONEBOT_RELAY_ONEBOT_API_BASE_URL:-$(read_env_value QQ_ONEBOT_RELAY_ONEBOT_API_BASE_URL || true)}"
ONEBOT_API_BASE_URL="${ONEBOT_API_BASE_URL:-http://127.0.0.1:5700}"
ONEBOT_ACCESS_TOKEN="${QQ_ONEBOT_RELAY_ONEBOT_ACCESS_TOKEN:-$(read_env_value QQ_ONEBOT_RELAY_ONEBOT_ACCESS_TOKEN || true)}"
if [[ -z "$ONEBOT_ACCESS_TOKEN" ]]; then
    ONEBOT_ACCESS_TOKEN="${GO_CQHTTP_ACCESS_TOKEN:-$(read_env_value GO_CQHTTP_ACCESS_TOKEN || true)}"
fi

python3 - "$ENV_FILE" "$TARGET_GROUP_NAME" "$ONEBOT_API_BASE_URL" "$ONEBOT_ACCESS_TOKEN" <<'PY'
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

env_path = Path(sys.argv[1])
target_group_name = sys.argv[2]
api_base = sys.argv[3].rstrip("/")
access_token = sys.argv[4]

url = f"{api_base}/get_group_list"
headers = {}
if access_token:
    headers["Authorization"] = f"Bearer {access_token}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}access_token={urllib.parse.quote(access_token)}"

req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=15) as resp:
    payload = json.load(resp)

groups = payload.get("data") or []
matched = None
for item in groups:
    if str(item.get("group_name") or "").strip() == target_group_name:
        matched = item
        break

if not matched:
    names = ", ".join(sorted({str(item.get('group_name') or '').strip() for item in groups if item.get('group_name')}))
    raise SystemExit(f"group not found: {target_group_name}; available: {names}")

group_id = str(matched.get("group_id") or "").strip()
if not group_id:
    raise SystemExit(f"group id missing for: {target_group_name}")

lines = []
if env_path.exists():
    lines = env_path.read_text(encoding="utf-8").splitlines()

updated = False
for idx, line in enumerate(lines):
    if line.startswith("QQ_ONEBOT_RELAY_ALLOWED_GROUP_IDS="):
        lines[idx] = f"QQ_ONEBOT_RELAY_ALLOWED_GROUP_IDS={group_id}"
        updated = True
        break

if not updated:
    lines.append(f"QQ_ONEBOT_RELAY_ALLOWED_GROUP_IDS={group_id}")

env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
print(group_id)
PY

if command -v sudo >/dev/null 2>&1; then
  sudo systemctl restart qq-onebot-relay
fi

echo "[qq-relay] bound group: $TARGET_GROUP_NAME"
echo "[qq-relay] relay restarted"