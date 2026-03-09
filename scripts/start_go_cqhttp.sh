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

GO_CQHTTP_HOME="${GO_CQHTTP_HOME:-$(read_env_value GO_CQHTTP_HOME || true)}"
GO_CQHTTP_HOME="${GO_CQHTTP_HOME:-$ROOT_DIR/runtime/go-cqhttp}"
GO_CQHTTP_EXTRA_ARGS="${GO_CQHTTP_EXTRA_ARGS:-$(read_env_value GO_CQHTTP_EXTRA_ARGS || true)}"
GO_CQHTTP_EXTRA_ARGS="${GO_CQHTTP_EXTRA_ARGS:-}"

mkdir -p "$GO_CQHTTP_HOME"
cd "$GO_CQHTTP_HOME"

if [[ ! -x "$GO_CQHTTP_HOME/go-cqhttp" ]]; then
  echo "go-cqhttp binary not found: $GO_CQHTTP_HOME/go-cqhttp" >&2
  echo "run: bash scripts/setup_go_cqhttp.sh" >&2
  exit 1
fi

if [[ ! -f "$GO_CQHTTP_HOME/config.yml" ]]; then
  echo "config not found: $GO_CQHTTP_HOME/config.yml" >&2
  echo "run: bash scripts/setup_go_cqhttp.sh" >&2
  exit 1
fi

exec "$GO_CQHTTP_HOME/go-cqhttp" ${GO_CQHTTP_EXTRA_ARGS}