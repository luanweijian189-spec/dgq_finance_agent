#!/usr/bin/env bash
set -euo pipefail

PUBLIC_IP="${PUBLIC_IP:-$(curl -s ifconfig.me || true)}"
cat <<EOF
=== 腾讯云公网访问检查 ===
公网 IP: ${PUBLIC_IP:-<unknown>}
页面地址: http://${PUBLIC_IP:-<your-public-ip>}/
健康检查: http://${PUBLIC_IP:-<your-public-ip>}/health

如果手机/电脑访问不了，通常不是服务没启动，而是腾讯云安全组没放行：
1. 入站放行 TCP 80   (前端页面)
2. 可选放行 TCP 8000 (直连调试)
3. 若后续上 HTTPS，再放行 TCP 443

当前服务器本机已完成：
- Nginx 监听 80
- FastAPI 监听 8000
- 基础认证已开启

浏览器登录信息：
- username=finance
- password=请查看 .env 中 WEB_BASIC_AUTH_PASSWORD
EOF
