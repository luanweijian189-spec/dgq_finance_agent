from __future__ import annotations

import json
import os
import sys

import requests


def main() -> int:
    base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000")

    def post(path: str, payload: dict):
        response = requests.post(f"{base_url}{path}", json=payload, timeout=8)
        response.raise_for_status()
        return response.json()

    def get(path: str):
        response = requests.get(f"{base_url}{path}", timeout=8)
        response.raise_for_status()
        return response.json()

    print("[1/5] health check")
    print(json.dumps(get("/api/system/check"), ensure_ascii=False, indent=2))

    print("[2/5] ingest message")
    ingest = post(
        "/api/messages/ingest",
        {
            "message": "看好600519，逻辑是业绩持续改善与估值修复",
            "recommender_name": "张三",
            "wechat_id": "wx_zhangsan",
            "source": "smoke",
        },
    )
    print(json.dumps(ingest, ensure_ascii=False, indent=2))

    print("[3/5] subscribe alert")
    alert = post(
        "/api/alerts/subscribe",
        {
            "stock_code": "600519",
            "subscriber": "smoke_user",
        },
    )
    print(json.dumps(alert, ensure_ascii=False, indent=2))

    print("[4/5] run daily evaluation")
    run = post("/api/evaluations/run", {})
    print(json.dumps(run, ensure_ascii=False, indent=2))

    print("[5/5] query status command")
    cmd = post("/api/commands", {"command": "/status 600519"})
    print(json.dumps(cmd, ensure_ascii=False, indent=2))

    print("E2E smoke done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
