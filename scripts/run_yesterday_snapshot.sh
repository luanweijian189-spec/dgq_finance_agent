#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
fi

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

"$PYTHON_BIN" -m pip install -r requirements.txt

SNAPSHOT_DB=""
for candidate in "$ROOT_DIR/demo.db" "$ROOT_DIR/data/backups/reset_input_import_20260308_164343/demo.db"; do
  if [[ -f "$candidate" ]]; then
    SNAPSHOT_DB="$candidate"
    break
  fi
done

if [[ -z "$SNAPSHOT_DB" ]]; then
  echo "[dgq] 未找到昨日快照数据库 demo.db"
  exit 1
fi

RUNTIME_DIR="$ROOT_DIR/data/runtime"
mkdir -p "$RUNTIME_DIR"
RUNTIME_DB="$RUNTIME_DIR/yesterday_snapshot.db"
cp "$SNAPSHOT_DB" "$RUNTIME_DB"

export APP_ENV="dev"
export DEBUG="true"
export SCHEDULER_ENABLED="false"
export DATABASE_URL="sqlite:///$RUNTIME_DB"
export RAG_STORE_PATH="$ROOT_DIR/data/research_notes.jsonl"
export STOCK_KNOWLEDGE_DIR="$ROOT_DIR/data/stocks"
export DAILY_REPORT_DIR="$ROOT_DIR/reports/daily"
export LLM_USAGE_STORE_PATH="$ROOT_DIR/data/llm_usage.jsonl"
export INTRADAY_CACHE_DIR="$ROOT_DIR/data/intraday"

echo "[dgq] 使用昨日快照数据库: $SNAPSHOT_DB"
echo "[dgq] 运行时数据库副本: $RUNTIME_DB"
echo "[dgq] 执行迁移检查..."
"$PYTHON_BIN" -m alembic upgrade head

echo "[dgq] 启动昨日快照服务：http://127.0.0.1:8000"
exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
