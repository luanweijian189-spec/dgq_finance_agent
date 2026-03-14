from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.llm_client import LLMApiClient


def main() -> int:
    settings = get_settings()
    client = LLMApiClient(
        api_base=settings.llm_api_base,
        api_key=settings.llm_api_key,
        model=settings.analysis_model,
        chat_path=settings.llm_api_chat_path,
        completions_path=settings.llm_api_completions_path,
        api_mode=settings.llm_api_mode,
        timeout_seconds=settings.llm_api_timeout_seconds,
        feature_name="smoke_llm",
    )
    print("llm.ready=", client.ready)
    print("llm.api_base=", settings.llm_api_base)
    print("llm.model=", settings.analysis_model)
    print("llm.mode=", settings.llm_api_mode)

    if not client.ready:
        print("LLM 配置不完整：请检查 LLM_API_BASE / LLM_API_KEY / ANALYSIS_MODEL")
        return 2

    reply = client.chat(
        messages=[
            {"role": "system", "content": "你是一个简洁的测试助手。"},
            {"role": "user", "content": "请回复：LLM链路已打通"},
        ],
        temperature=0,
    )

    if not reply:
        print("调用失败：返回为空，请检查 baseUrl/apiKey/model 或 endpoint 模式")
        return 1

    print("reply=", reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
