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

    market_data_provider: str = "baostock"
    news_data_provider: str = "tushare"
    tushare_token: str = ""
    news_webhook_url: str = ""
    news_site_whitelist: str = (
        "https://www.eastmoney.com,https://finance.sina.com.cn,https://www.stcn.com,https://www.cnstock.com"
    )
    news_site_timeout: int = 5

    alert_webhook_url: str = ""

    rag_store_path: str = "data/research_notes.jsonl"
    stock_knowledge_dir: str = "data/stocks"
    daily_report_dir: str = "reports/daily"

    analysis_model: str = "rule"
    llm_api_base: str = ""
    llm_api_key: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
