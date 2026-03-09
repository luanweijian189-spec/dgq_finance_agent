#!/usr/bin/env bash
set -euo pipefail

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo not found" >&2
  exit 1
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y openjdk-11-jre-headless unzip curl

java -version

cat <<EOF

Java runtime ready.
Next step still requires a compatible qsign package or service URL.
After you have one, write these into .env and rerun:

GO_CQHTTP_SIGN_SERVER_URL=http://127.0.0.1:8080
GO_CQHTTP_SIGN_SERVER_KEY=114514
GO_CQHTTP_SIGN_SERVER_AUTHORIZATION=-

Then execute:
bash scripts/setup_go_cqhttp.sh
sudo systemctl restart go-cqhttp
EOF