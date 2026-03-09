#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
USERNAME="${WEB_USERNAME:-finance}"
PASSWORD="${WEB_PASSWORD:-}"

if [[ -z "$PASSWORD" ]]; then
  PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(16))
PY
)"
fi

ensure_line() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

ensure_line WEB_BASIC_AUTH_ENABLED true
ensure_line WEB_BASIC_AUTH_USERNAME "$USERNAME"
ensure_line WEB_BASIC_AUTH_PASSWORD "$PASSWORD"

echo "[ok] 已启用前端基础认证"
echo "username=$USERNAME"
echo "password=$PASSWORD"
echo "下一步：sudo systemctl restart dgq-finance-agent"
