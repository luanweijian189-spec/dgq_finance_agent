#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

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

QQ_ONEBOT_RELAY_PORT="${QQ_ONEBOT_RELAY_PORT:-$(read_env_value QQ_ONEBOT_RELAY_PORT || true)}"
QQ_ONEBOT_RELAY_PORT="${QQ_ONEBOT_RELAY_PORT:-5701}"
QQ_ONEBOT_RELAY_SHARED_TOKEN="${QQ_ONEBOT_RELAY_SHARED_TOKEN:-$(read_env_value QQ_ONEBOT_RELAY_SHARED_TOKEN || true)}"
CONNECTOR_SHARED_TOKEN="${CONNECTOR_SHARED_TOKEN:-$(read_env_value CONNECTOR_SHARED_TOKEN || true)}"
GO_CQHTTP_HOME="${GO_CQHTTP_HOME:-$(read_env_value GO_CQHTTP_HOME || true)}"
GO_CQHTTP_HOME="${GO_CQHTTP_HOME:-$ROOT_DIR/runtime/go-cqhttp}"
GO_CQHTTP_QQ="${GO_CQHTTP_QQ:-$(read_env_value GO_CQHTTP_QQ || true)}"
GO_CQHTTP_QQ="${GO_CQHTTP_QQ:-1612085779}"
GO_CQHTTP_HTTP_API_ADDRESS="${GO_CQHTTP_HTTP_API_ADDRESS:-$(read_env_value GO_CQHTTP_HTTP_API_ADDRESS || true)}"
GO_CQHTTP_HTTP_API_ADDRESS="${GO_CQHTTP_HTTP_API_ADDRESS:-127.0.0.1:5700}"
GO_CQHTTP_POST_URL="${GO_CQHTTP_POST_URL:-$(read_env_value GO_CQHTTP_POST_URL || true)}"
GO_CQHTTP_POST_URL="${GO_CQHTTP_POST_URL:-http://127.0.0.1:${QQ_ONEBOT_RELAY_PORT}/webhook}"
GO_CQHTTP_ACCESS_TOKEN="${GO_CQHTTP_ACCESS_TOKEN:-$(read_env_value GO_CQHTTP_ACCESS_TOKEN || true)}"
GO_CQHTTP_ACCESS_TOKEN="${GO_CQHTTP_ACCESS_TOKEN:-${QQ_ONEBOT_RELAY_SHARED_TOKEN:-${CONNECTOR_SHARED_TOKEN:-}}}"
GO_CQHTTP_DOWNLOAD_URL="${GO_CQHTTP_DOWNLOAD_URL:-$(read_env_value GO_CQHTTP_DOWNLOAD_URL || true)}"
GO_CQHTTP_DOWNLOAD_URL="${GO_CQHTTP_DOWNLOAD_URL:-}"
GO_CQHTTP_SIGN_SERVER_URL="${GO_CQHTTP_SIGN_SERVER_URL:-$(read_env_value GO_CQHTTP_SIGN_SERVER_URL || true)}"
GO_CQHTTP_SIGN_SERVER_KEY="${GO_CQHTTP_SIGN_SERVER_KEY:-$(read_env_value GO_CQHTTP_SIGN_SERVER_KEY || true)}"
GO_CQHTTP_SIGN_SERVER_AUTHORIZATION="${GO_CQHTTP_SIGN_SERVER_AUTHORIZATION:-$(read_env_value GO_CQHTTP_SIGN_SERVER_AUTHORIZATION || true)}"
GO_CQHTTP_SIGN_SERVER_AUTHORIZATION="${GO_CQHTTP_SIGN_SERVER_AUTHORIZATION:--}"
GO_CQHTTP_SIGN_AUTO_REGISTER="${GO_CQHTTP_SIGN_AUTO_REGISTER:-$(read_env_value GO_CQHTTP_SIGN_AUTO_REGISTER || true)}"
GO_CQHTTP_SIGN_AUTO_REGISTER="${GO_CQHTTP_SIGN_AUTO_REGISTER:-true}"
GO_CQHTTP_SIGN_AUTO_REFRESH_TOKEN="${GO_CQHTTP_SIGN_AUTO_REFRESH_TOKEN:-$(read_env_value GO_CQHTTP_SIGN_AUTO_REFRESH_TOKEN || true)}"
GO_CQHTTP_SIGN_AUTO_REFRESH_TOKEN="${GO_CQHTTP_SIGN_AUTO_REFRESH_TOKEN:-true}"
GO_CQHTTP_SIGN_REFRESH_INTERVAL="${GO_CQHTTP_SIGN_REFRESH_INTERVAL:-$(read_env_value GO_CQHTTP_SIGN_REFRESH_INTERVAL || true)}"
GO_CQHTTP_SIGN_REFRESH_INTERVAL="${GO_CQHTTP_SIGN_REFRESH_INTERVAL:-40}"
GO_CQHTTP_SIGN_IS_BELOW_110="${GO_CQHTTP_SIGN_IS_BELOW_110:-$(read_env_value GO_CQHTTP_SIGN_IS_BELOW_110 || true)}"
GO_CQHTTP_SIGN_IS_BELOW_110="${GO_CQHTTP_SIGN_IS_BELOW_110:-false}"

mkdir -p "$GO_CQHTTP_HOME"

ensure_watch_protocol() {
  python3 - "$GO_CQHTTP_HOME/device.json" <<'PY'
import json
import sys
from pathlib import Path

device_path = Path(sys.argv[1])
if not device_path.exists():
  raise SystemExit(0)

try:
  data = json.loads(device_path.read_text(encoding="utf-8"))
except Exception as exc:
  raise SystemExit(f"invalid device.json: {exc}")

if data.get("protocol") != 2:
  data["protocol"] = 2
  device_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
  print("[go-cqhttp] device.json protocol switched to Android Watch (2)")
PY
}

download_and_extract() {
  local url="$1"
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  echo "[go-cqhttp] downloading: $url"
  curl -L --fail "$url" -o "$tmpdir/pkg"

  python3 - "$tmpdir/pkg" "$GO_CQHTTP_HOME" <<'PY'
import os
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

pkg = Path(sys.argv[1])
target = Path(sys.argv[2])
work = target / ".extract"
if work.exists():
    import shutil
    shutil.rmtree(work)
work.mkdir(parents=True, exist_ok=True)

data = pkg.read_bytes()
if tarfile.is_tarfile(pkg):
    with tarfile.open(pkg) as tf:
        tf.extractall(work)
elif zipfile.is_zipfile(pkg):
    with zipfile.ZipFile(pkg) as zf:
        zf.extractall(work)
else:
    candidate = target / "go-cqhttp"
    candidate.write_bytes(data)
    candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(candidate)
    sys.exit(0)

candidates = []
for path in work.rglob("*"):
    if path.is_file() and path.name == "go-cqhttp":
        candidates.append(path)

if not candidates:
    raise SystemExit("go-cqhttp binary not found in archive")

binary = candidates[0]
dest = target / "go-cqhttp"
dest.write_bytes(binary.read_bytes())
dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print(dest)
PY
}

resolve_download_url() {
  if [[ -n "$GO_CQHTTP_DOWNLOAD_URL" ]]; then
    printf '%s\n' "$GO_CQHTTP_DOWNLOAD_URL"
    return 0
  fi

  python3 <<'PY'
import json
import sys
import urllib.request

api = "https://api.github.com/repos/Mrs4s/go-cqhttp/releases/latest"
with urllib.request.urlopen(api, timeout=20) as resp:
    data = json.load(resp)

assets = data.get("assets", [])
preferred = []
fallback = []
for asset in assets:
    url = str(asset.get("browser_download_url") or "")
    name = str(asset.get("name") or "").lower()
    if "linux" not in name or "amd64" not in name:
      continue
    if name.endswith(".tar.gz"):
      preferred.append(url)
    elif name.endswith(".zip") or name.endswith(".gz"):
      fallback.append(url)

for url in preferred + fallback:
    if url:
      print(url)
      raise SystemExit(0)

raise SystemExit("unable to find linux amd64 release asset; set GO_CQHTTP_DOWNLOAD_URL manually")
PY
}

if [[ ! -x "$GO_CQHTTP_HOME/go-cqhttp" ]]; then
  DOWNLOAD_URL="$(resolve_download_url)"
  download_and_extract "$DOWNLOAD_URL"
else
  echo "[go-cqhttp] binary already exists: $GO_CQHTTP_HOME/go-cqhttp"
fi

cat > "$GO_CQHTTP_HOME/config.yml" <<EOF
account:
  uin: ${GO_CQHTTP_QQ}
  password: ''
  encrypt: false
  status: 0
  relogin:
    delay: 3
    interval: 3
    max-times: 0
  use-sso-address: true
  allow-temp-session: false
EOF

if [[ -n "$GO_CQHTTP_SIGN_SERVER_URL" ]]; then
  cat >> "$GO_CQHTTP_HOME/config.yml" <<EOF
  sign-servers:
    - url: '${GO_CQHTTP_SIGN_SERVER_URL}'
      key: '${GO_CQHTTP_SIGN_SERVER_KEY}'
      authorization: '${GO_CQHTTP_SIGN_SERVER_AUTHORIZATION}'
  rule-change-sign-server: 1
  max-check-count: 0
  sign-server-timeout: 60
  is-below-110: ${GO_CQHTTP_SIGN_IS_BELOW_110}
  auto-register: ${GO_CQHTTP_SIGN_AUTO_REGISTER}
  auto-refresh-token: ${GO_CQHTTP_SIGN_AUTO_REFRESH_TOKEN}
  refresh-interval: ${GO_CQHTTP_SIGN_REFRESH_INTERVAL}
EOF
fi

cat >> "$GO_CQHTTP_HOME/config.yml" <<EOF

heartbeat:
  interval: 5

message:
  post-format: string
  ignore-invalid-cqcode: false
  force-fragment: false
  fix-url: false
  proxy-rewrite: ''
  report-self-message: false
  remove-reply-at: false
  extra-reply-data: false
  skip-mime-scan: false
  convert-webp-image: false
  http-timeout: 15

output:
  log-level: info
  log-aging: 15
  log-force-new: true
  debug: false

default-middlewares: &default
  access-token: '${GO_CQHTTP_ACCESS_TOKEN}'
  filter: ''
  rate-limit:
    enabled: false
    frequency: 1
    bucket: 1

servers:
  - http:
      address: ${GO_CQHTTP_HTTP_API_ADDRESS}
      version: 11
      timeout: 15
      long-polling:
        enabled: false
        max-queue-size: 2000
      middlewares:
        <<: *default
      post:
        - url: ${GO_CQHTTP_POST_URL}
          secret: ''
          max-retries: 10
          retries-interval: 1000
EOF

echo "[go-cqhttp] config written: $GO_CQHTTP_HOME/config.yml"
ensure_watch_protocol

if command -v sudo >/dev/null 2>&1; then
  sudo install -m 0644 "$ROOT_DIR/deploy/tencent/go-cqhttp.service" /etc/systemd/system/go-cqhttp.service
  sudo systemctl daemon-reload
  sudo systemctl enable go-cqhttp >/dev/null 2>&1 || true
  echo "[go-cqhttp] systemd service installed: go-cqhttp"
fi

cat <<EOF

ready:
1. start service: sudo systemctl start go-cqhttp
2. watch qr/log:   sudo journalctl -u go-cqhttp -f
3. after scan:     bash scripts/bind_qq_group_by_name.sh finance-robot
EOF