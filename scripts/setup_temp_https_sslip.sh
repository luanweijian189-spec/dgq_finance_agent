#!/usr/bin/env bash
set -euo pipefail

APP_NAME="dgq-finance-agent"
APP_PORT="${APP_PORT:-8000}"
PUBLIC_IP="${PUBLIC_IP:-}"
TEMP_DOMAIN="${TEMP_DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
NGINX_CONF_PATH="/etc/nginx/sites-available/${APP_NAME}.conf"
NGINX_ENABLED_PATH="/etc/nginx/sites-enabled/${APP_NAME}.conf"

log() {
  printf '[temp-https] %s\n' "$*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    log "请用 sudo 运行此脚本"
    exit 1
  fi
}

detect_public_ip() {
  if [[ -n "$PUBLIC_IP" ]]; then
    return 0
  fi

  PUBLIC_IP="$(curl -4fsS https://ifconfig.me 2>/dev/null || true)"
  if [[ -z "$PUBLIC_IP" ]]; then
    PUBLIC_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi

  if [[ -z "$PUBLIC_IP" ]]; then
    log "无法自动识别公网 IP，请手动传入 PUBLIC_IP=58.87.91.152"
    exit 1
  fi
}

build_temp_domain() {
  if [[ -n "$TEMP_DOMAIN" ]]; then
    return 0
  fi

  TEMP_DOMAIN="${PUBLIC_IP//./-}.sslip.io"
}

install_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y nginx certbot python3-certbot-nginx
}

write_nginx_http_conf() {
  cat > "$NGINX_CONF_PATH" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${TEMP_DOMAIN};

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
    }
}
EOF
}

enable_nginx_site() {
  ln -sf "$NGINX_CONF_PATH" "$NGINX_ENABLED_PATH"
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl reload nginx
}

issue_certificate() {
  local email_args=()

  if [[ -n "$LETSENCRYPT_EMAIL" ]]; then
    email_args=(--email "$LETSENCRYPT_EMAIL")
  else
    log "未提供 LETSENCRYPT_EMAIL，将使用无邮箱注册模式"
    email_args=(--register-unsafely-without-email)
  fi

  certbot --nginx \
    -d "$TEMP_DOMAIN" \
    --non-interactive \
    --agree-tos \
    "${email_args[@]}" \
    --redirect
}

print_summary() {
  cat <<EOF

=== 临时 HTTPS 已配置 ===
临时域名: https://${TEMP_DOMAIN}
QQ 官方回调: https://${TEMP_DOMAIN}/api/connectors/qq/official/webhook

说明:
1. 这是基于公网 IP 生成的临时域名，适合先验证整体链路。
2. 证书由 Let's Encrypt 免费签发。
3. 后续正式上线时，再替换成你自己的域名即可。
EOF
}

require_root
detect_public_ip
build_temp_domain

log "准备为 ${TEMP_DOMAIN} 配置临时 HTTPS"
install_packages
write_nginx_http_conf
enable_nginx_site
issue_certificate
print_summary