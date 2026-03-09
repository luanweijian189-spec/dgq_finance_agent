#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
fi

PORT="${QQ_ONEBOT_RELAY_PORT:-5701}"
exec "$PYTHON_BIN" -m uvicorn connectors.qq_onebot_relay.main:app --host 0.0.0.0 --port "$PORT"
