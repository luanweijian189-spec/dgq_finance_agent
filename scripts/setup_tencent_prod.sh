#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
ENV_TEMPLATE="${ENV_TEMPLATE:-$ROOT_DIR/.env.tencent.example}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
USE_SYSTEM_PYTHON="${USE_SYSTEM_PYTHON:-0}"
SKIP_PIP="${SKIP_PIP:-0}"
SKIP_MIGRATION="${SKIP_MIGRATION:-0}"

log() {
  printf '[setup] %s\n' "$*"
}

ensure_line() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

random_token() {
  "$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

db_ready() {
  "$RUN_PYTHON" - <<'PY'
from app.config import get_settings
from sqlalchemy import create_engine, text

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
PY
}

maybe_start_docker_db() {
  if ! command -v docker >/dev/null 2>&1; then
    return 1
  fi
  if [[ ! -f "$ROOT_DIR/docker-compose.yml" ]]; then
    return 1
  fi
  log "检测到 Docker，尝试启动 docker compose db"
  (cd "$ROOT_DIR" && docker compose up -d db)
}

database_url() {
  grep -E '^DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-
}

log "root=$ROOT_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  log "未发现 .env，使用 $ENV_TEMPLATE 生成"
  cp "$ENV_TEMPLATE" "$ENV_FILE"
fi

RUN_PYTHON="$PYTHON_BIN"
RUN_PIP="$PYTHON_BIN -m pip"

if [[ "$USE_SYSTEM_PYTHON" != "1" ]]; then
  if [[ -d "$VENV_DIR" && ( ! -x "$VENV_DIR/bin/python" || ! -x "$VENV_DIR/bin/pip" ) ]]; then
    log "检测到损坏的虚拟环境，删除后重建: $VENV_DIR"
    rm -rf "$VENV_DIR"
  fi
  if [[ ! -d "$VENV_DIR" ]]; then
    log "创建虚拟环境 $VENV_DIR"
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
      log "创建虚拟环境失败。Ubuntu 通常需要先安装 python3-venv：sudo apt-get install -y python3-venv"
      log "也可临时改用系统 Python：USE_SYSTEM_PYTHON=1 bash scripts/setup_tencent_prod.sh"
      exit 1
    fi
  fi
  RUN_PYTHON="$VENV_DIR/bin/python"
  RUN_PIP="$VENV_DIR/bin/pip"
else
  log "USE_SYSTEM_PYTHON=1，跳过 venv，直接使用系统 Python"
fi

if [[ "$SKIP_PIP" != "1" ]]; then
  log "安装 Python 依赖"
  bash -lc "$RUN_PIP install --upgrade pip"
  bash -lc "$RUN_PIP install -r '$ROOT_DIR/requirements.txt'"
fi

if ! grep -q '^CONNECTOR_SHARED_TOKEN=' "$ENV_FILE" \
  || grep -q '^CONNECTOR_SHARED_TOKEN=REPLACE_WITH_RANDOM_TOKEN$' "$ENV_FILE" \
  || grep -q '^CONNECTOR_SHARED_TOKEN=$' "$ENV_FILE"
then
  token="$(random_token)"
  log "生成 CONNECTOR_SHARED_TOKEN"
  ensure_line CONNECTOR_SHARED_TOKEN "$token"
fi

ensure_line APP_ENV prod
ensure_line DEBUG false
ensure_line OPENCLAW_NOTIFIER_ENABLED true
ensure_line OPENCLAW_CHANNEL qq
ensure_line SCHEDULER_INTRADAY_REFRESH_ENABLED true

if [[ "$SKIP_MIGRATION" != "1" ]]; then
  db_url="$(database_url)"
  if [[ "$db_url" == postgresql* ]] && ! db_ready; then
    maybe_start_docker_db >/dev/null 2>&1 || true
    if [[ "$db_url" == postgresql* ]] && ! db_ready; then
      log "数据库尚未就绪：$(database_url)"
      log "如果当前机器还没装 PostgreSQL，请先执行：sudo bash scripts/install_tencent_postgres.sh"
      log "如果机器已安装 Docker，也可执行：docker compose up -d db"
      log "数据库启动后，再执行：SKIP_PIP=1 bash scripts/setup_tencent_prod.sh"
      exit 1
    fi
  fi
  log "执行 Alembic 迁移"
  bash -lc "$RUN_PYTHON -m alembic upgrade head"
fi

log "完成。建议后续执行："
log "  1) 检查 .env 中 DATABASE_URL / TUSHARE_TOKEN / LLM_API_* / OPENCLAW_* / QQ_OFFICIAL_BOT_*"
log "  2) 安装 systemd 模板: deploy/tencent/dgq-finance-agent.service"
log "  3) 配置 Nginx: deploy/tencent/nginx.dgq-finance-agent.conf"
log "  4) 官方 QQ Bot 联调: scripts/print_qq_official_bot_setup_info.sh"
log "  5) 官方 QQ Bot 冒烟: scripts/smoke_qq_official_webhook.sh"
log "  6) OpenClaw 回调兼容冒烟: scripts/smoke_openclaw_qq_webhook.sh"
