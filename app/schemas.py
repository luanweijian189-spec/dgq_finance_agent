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


class AgentMatrixTaskCreateRequest(BaseModel):
    objective: str
    context: str = ""
    operator: str = "api_user"
    source: str = "api"
    conversation_id: str = ""
    branch: str = ""
    auto_dispatch: bool = False
    auto_check: bool = False


class AgentMatrixTaskActionRequest(BaseModel):
    auto_check: bool = False


class RepoOpsTaskCreateRequest(BaseModel):
    objective: str
    context: str = ""
    operator: str = "api_user"
    source: str = "api"
    conversation_id: str = ""
    linked_agent_task_id: str = ""
    target_branch: str = ""
    auto_plan: bool = True
    auto_execute: bool = False


class RepoOpsTaskActionRequest(BaseModel):
    note: str = ""


class AlertRequest(BaseModel):
    stock_code: str = Field(pattern=r"^(60|00|30|68)\d{4}$")
    subscriber: str


class CommandResponse(BaseModel):
    result: str


class NewsScanRequest(BaseModel):
    min_score: float = 2.5
    auto_promote: bool = False
    auto_promote_min_score: float = 3.8
    limit: int = 40


class NewsScanResponse(BaseModel):
    scan_date: str
    raw_discovered: int
    saved_candidates: int
    promoted: int
    updated_tracking: int
    min_score: float
    auto_promote: bool
    auto_promote_min_score: float


class NewsCandidatePromoteRequest(BaseModel):
    candidate_id: int


class NewsCandidateListResponse(BaseModel):
    items: list[dict]


class IntradayBarItem(BaseModel):
    timestamp: str
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float
    amount: float
    amplitude: float = 0.0
    change_percent: float = 0.0
    change_amount: float = 0.0
    turnover_rate: float = 0.0


class IntradayBarsResponse(BaseModel):
    stock_code: str
    source: str
    period: str
    adjust: str = ""
    used_cache: bool = False
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    bars: list[IntradayBarItem]


class IntradayTradeItem(BaseModel):
    timestamp: str
    price: float
    volume_lot: float
    side: str = ""


class IntradayTradesResponse(BaseModel):
    stock_code: str
    source: str
    used_cache: bool = False
    trades: list[IntradayTradeItem]


class IntradaySyncResponse(BaseModel):
    stock_code: str
    stock_name: str = ""
    source: str
    period: str
    adjust: str = ""
    used_cache: bool = False
    saved_bars: int = 0
    saved_ticks: int = 0
    latest_bar_timestamp: str = ""
    latest_tick_timestamp: str = ""


class IntradayBatchSyncRequest(BaseModel):
    stock_codes: list[str] = Field(default_factory=list)
    period: str = "1"
    adjust: str = ""
    include_ticks: bool = True
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    limit: int = 10


class IntradayBatchSyncItem(BaseModel):
    stock_code: str
    stock_name: str = ""
    ok: bool
    message: str = ""
    saved_bars: int = 0
    saved_ticks: int = 0
    latest_bar_timestamp: str = ""


class IntradayBatchSyncResponse(BaseModel):
    source: str
    period: str
    adjust: str = ""
    total_requested: int
    success_count: int
    failed_count: int
    items: list[IntradayBatchSyncItem]


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
