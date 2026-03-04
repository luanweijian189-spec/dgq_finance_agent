from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "dgq-finance-agent"
    app_env: str = "dev"
    debug: bool = False

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/dgq_finance_agent"
    )

    scheduler_enabled: bool = True
    scheduler_cron: str = "30 15 * * 1-5"
    scheduler_news_scan_enabled: bool = True
    scheduler_news_scan_cron: str = "*/20 9-15 * * 1-5"

    market_data_provider: str = "baostock"
    news_data_provider: str = "tushare"
    tushare_token: str = ""
    news_webhook_url: str = ""
    news_site_whitelist: str = (
        "https://www.eastmoney.com,https://finance.sina.com.cn,https://www.stcn.com,https://www.cnstock.com"
    )
    news_site_timeout: int = 5
    news_discovery_min_score: float = 2.5
    news_auto_promote_min_score: float = 3.8

    alert_webhook_url: str = ""

    rag_store_path: str = "data/research_notes.jsonl"
    stock_knowledge_dir: str = "data/stocks"
    daily_report_dir: str = "reports/daily"
    memory_backend: str = "jsonl"
    memory_retrieval_limit: int = 8

    analysis_model: str = "gpt-5.3-codex"
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_api_chat_path: str = "/chat/completions"
    llm_api_timeout_seconds: int = 15


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
