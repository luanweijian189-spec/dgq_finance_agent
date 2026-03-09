#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y postgresql postgresql-contrib
systemctl enable --now postgresql

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='postgres'" | grep -q 1 || true
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'postgres';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='dgq_finance_agent'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE dgq_finance_agent OWNER postgres;"

printf '\n[ok] PostgreSQL 已启动，数据库 dgq_finance_agent 已就绪，默认连接：postgresql+psycopg://postgres:postgres@127.0.0.1:5432/dgq_finance_agent\n'
