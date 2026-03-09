#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  python3-pip \
  python3-venv \
  nginx

printf '\n[ok] 已安装基础系统依赖: python3-pip python3-venv nginx\n'
