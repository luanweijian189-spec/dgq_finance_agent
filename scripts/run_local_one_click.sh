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

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt

mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/reports/daily"

export APP_ENV="dev"
export DEBUG="true"
export SCHEDULER_ENABLED="false"

DB_MODE="sqlite"
DB_FILE="$ROOT_DIR/data/local_dev.db"
export DATABASE_URL="sqlite:///$DB_FILE"

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    echo "[dgq] 检测到 Docker，可用 PostgreSQL 容器启动数据库"
    docker compose up -d db
    DB_MODE="postgres"
    export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/dgq_finance_agent"
  else
    echo "[dgq] 检测到 docker 命令，但 Docker daemon 未启动，回退到 SQLite"
  fi
else
  echo "[dgq] 未检测到 Docker，回退到 SQLite"
fi

echo "[dgq] 当前数据库模式: $DB_MODE"
echo "[dgq] 当前 DATABASE_URL: $DATABASE_URL"

echo "[dgq] 执行数据库迁移..."
"$PYTHON_BIN" -m alembic upgrade head

echo "[dgq] 启动服务：http://127.0.0.1:8000"
exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
