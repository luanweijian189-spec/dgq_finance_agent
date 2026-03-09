from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "dgq-finance-agent"
    app_env: str = "prod"
    debug: bool = False

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/dgq_finance_agent"
    )

    scheduler_enabled: bool = True
    scheduler_cron: str = "30 15 * * 1-5"
    scheduler_news_scan_enabled: bool = True
    scheduler_news_scan_cron: str = "*/20 9-15 * * 1-5"
    scheduler_intraday_refresh_enabled: bool = False
    scheduler_intraday_refresh_cron: str = "*/5 9-15 * * 1-5"
    scheduler_intraday_refresh_limit: int = 12
    scheduler_intraday_refresh_min_change_percent: float = 0.8
    scheduler_intraday_refresh_force_notify: bool = False

    market_data_provider: str = "baostock"
    intraday_data_provider: str = "freebest"
    news_data_provider: str = "sites"
    tushare_token: str = ""
    intraday_cache_dir: str = "data/intraday"
    intraday_request_interval_seconds: float = 1.2
    intraday_max_retries: int = 2
    intraday_pytdx_hosts: str = ""
    intraday_pytdx_bar_count: int = 800
    intraday_pytdx_tick_limit: int = 2000
    news_webhook_url: str = ""
    news_site_whitelist: str = (
        "https://www.eastmoney.com,https://finance.sina.com.cn,https://www.stcn.com,https://www.cnstock.com"
    )
    news_site_timeout: int = 5
    news_discovery_min_score: float = 2.5
    news_auto_promote_min_score: float = 3.8

    alert_webhook_url: str = ""
    openclaw_notifier_enabled: bool = False
    openclaw_command: str = "openclaw"
    openclaw_profile: str = "dev"
    openclaw_channel: str = "qq"
    openclaw_recipient: str = ""
    openclaw_timeout_seconds: int = 30
    qq_bot_enabled: bool = False
    qq_bot_base_url: str = ""
    qq_bot_target_type: str = "group"
    qq_bot_target_id: str = ""
    qq_bot_access_token: str = ""
    qq_official_bot_enabled: bool = False
    qq_official_bot_app_id: str = ""
    qq_official_bot_app_secret: str = ""
    qq_official_bot_api_base_url: str = "https://api.sgroup.qq.com"
    qq_official_bot_token_url: str = "https://bots.qq.com/app/getAppAccessToken"
    qq_official_bot_target_type: str = "group"
    qq_official_bot_target_id: str = ""
    qq_official_bot_timeout_seconds: int = 10
    connector_shared_token: str = ""
    web_basic_auth_enabled: bool = False
    web_basic_auth_username: str = "admin"
    web_basic_auth_password: str = ""
    web_basic_auth_exempt_paths: str = "/health,/api/connectors/openclaw/webhook,/api/connectors/qq/webhook,/api/connectors/qq/official/webhook,/api/connectors/wechat/webhook"

    rag_store_path: str = "data/research_notes.jsonl"
    stock_knowledge_dir: str = "data/stocks"
    daily_report_dir: str = "reports/daily"
    llm_usage_store_path: str = "data/llm_usage.jsonl"
    memory_backend: str = "jsonl"
    memory_retrieval_limit: int = 8

    analysis_model: str = "qwen2.5:3b"
    llm_api_base: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = ""
    llm_api_chat_path: str = "/chat/completions"
    llm_api_timeout_seconds: int = 15


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_basic_auth_exempt_paths(settings: Settings) -> List[str]:
    return [item.strip() for item in str(settings.web_basic_auth_exempt_paths or "").split(",") if item.strip()]
