from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class IngestMessageRequest(BaseModel):
    message: str
    recommender_name: str
    wechat_id: str = ""
    recommend_ts: Optional[datetime] = None
    source: str = "wechat"


class BulkImportTextRequest(BaseModel):
    raw_text: str
    default_recommender_name: str = "群友"
    source: str = "manual_bulk"


class BulkImportTextResponse(BaseModel):
    total_records: int
    created: int
    duplicates: int
    ignored: int
    rag_notes: int
    recommendation_ids: list[int]


class ResearchTextRequest(BaseModel):
    text: str
    operator_name: str = "研究员"
    source: str = "manual_research"


class ManualRecommendationRequest(BaseModel):
    stock_code: str = Field(pattern=r"^(60|00|30|68)\d{4}$")
    stock_name: str = ""
    logic: str
    recommender_name: str
    wechat_id: str = ""
    recommend_ts: Optional[datetime] = None


class DailyEvaluationRequest(BaseModel):
    recommendation_id: int
    date: Optional[date] = None
    close_price: float
    high_price: float
    low_price: float
    pnl_percent: float
    max_drawdown: float
    sharpe_ratio: float = 0.0
    logic_validated: bool = False
    market_cap_score: float = 50.0
    elasticity_score: float = 50.0
    liquidity_score: float = 50.0
    notes: str = ""


class CommandRequest(BaseModel):
    command: str


class AlertRequest(BaseModel):
    stock_code: str = Field(pattern=r"^(60|00|30|68)\d{4}$")
    subscriber: str


class CommandResponse(BaseModel):
    result: str


class DashboardMetric(BaseModel):
    stock_pool_size: int
    recommender_count: int
    avg_stock_score: float
    avg_reliability_score: float


class StockSummary(BaseModel):
    stock_code: str
    stock_name: str = ""
    latest_score: float
    latest_pnl: float
    latest_date: date


class RecommenderSummary(BaseModel):
    name: str
    reliability_score: float
    recommendation_count: int
