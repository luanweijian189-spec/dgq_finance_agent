from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Recommender:
    id: int
    name: str
    wechat_id: str = ""
    reliability_score: float = 50.0
    notes: str = ""


@dataclass
class Stock:
    id: int
    stock_code: str
    stock_name: str = ""
    industry: str = ""


@dataclass
class Recommendation:
    id: int
    stock_id: int
    recommender_id: int
    recommend_ts: datetime
    initial_price: float | None
    original_message: str
    extracted_logic: str
    status: str = "tracking"


@dataclass
class DailyPerformance:
    id: int
    recommendation_id: int
    date: date
    close_price: float
    high_price: float
    low_price: float
    pnl_percent: float
    max_drawdown: float
    evaluation_score: float
    sharpe_ratio: float = 0.0
    notes: str = ""
    extra: dict[str, float | str | bool] = field(default_factory=dict)
