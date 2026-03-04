#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-dev}"
TOKEN_FILE="${OPENCLAW_TOKEN_FILE:-$HOME/.openclaw-${PROFILE}/telegram_bot_token.txt}"

echo "[1/6] 检查 openclaw CLI..."
command -v openclaw >/dev/null
openclaw --version

echo "[2/6] 确保网关为 local 模式并运行..."
openclaw --${PROFILE} config set gateway.mode local >/dev/null || true
openclaw --${PROFILE} daemon install >/dev/null || true
openclaw --${PROFILE} daemon restart >/dev/null

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "未找到 token 文件: $TOKEN_FILE"
  echo "请先执行："
  echo "  mkdir -p \"$(dirname "$TOKEN_FILE")\""
  echo "  printf '123456:ABC-YourTelegramBotToken' > \"$TOKEN_FILE\""
  echo "然后重试本脚本。"
  exit 1
fi

echo "[3/6] 配置 Telegram 频道..."
openclaw --${PROFILE} channels add --channel telegram --token-file "$TOKEN_FILE"

echo "[4/6] 查看频道列表..."
openclaw --${PROFILE} channels list

echo "[5/6] 进行凭据探测..."
openclaw --${PROFILE} channels status --probe

echo "[6/6] 完成。下一步："
echo "  1) 在 Telegram 给你的 Bot 发一条消息（先 /start）"
echo "  2) 用下面命令触发一次 Agent："
echo "     openclaw --${PROFILE} agent --channel telegram --deliver -m '你好，回我一句已联通'"
echo "  3) 如需指定会话："
echo "     openclaw --${PROFILE} agent --to <telegram_user_or_chat_id> --channel telegram --deliver -m '测试消息'"
