from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Recommender(Base):
    __tablename__ = "recommenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    wechat_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    reliability_score: Mapped[float] = mapped_column(Float, default=50.0, server_default="50")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    recommendations: Mapped[list[Recommendation]] = relationship(back_populates="recommender")


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_stocks_stock_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    industry: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    recommendations: Mapped[list[Recommendation]] = relationship(back_populates="stock")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    recommender_id: Mapped[int] = mapped_column(
        ForeignKey("recommenders.id", ondelete="CASCADE"), index=True
    )
    recommend_ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    initial_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    original_message: Mapped[str] = mapped_column(Text)
    extracted_logic: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(32), default="tracking", server_default="tracking")
    source: Mapped[str] = mapped_column(String(32), default="wechat", server_default="wechat")

    stock: Mapped[Stock] = relationship(back_populates="recommendations")
    recommender: Mapped[Recommender] = relationship(back_populates="recommendations")
    daily_performance: Mapped[list[DailyPerformance]] = relationship(back_populates="recommendation")


class DailyPerformance(Base):
    __tablename__ = "daily_performance"
    __table_args__ = (
        UniqueConstraint("recommendation_id", "date", name="uq_daily_performance_reco_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date, index=True)
    close_price: Mapped[float] = mapped_column(Float)
    high_price: Mapped[float] = mapped_column(Float)
    low_price: Mapped[float] = mapped_column(Float)
    pnl_percent: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    evaluation_score: Mapped[float] = mapped_column(Float)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    logic_validated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    market_cap_score: Mapped[float] = mapped_column(Float, default=50.0, server_default="50")
    elasticity_score: Mapped[float] = mapped_column(Float, default=50.0, server_default="50")
    liquidity_score: Mapped[float] = mapped_column(Float, default=50.0, server_default="50")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")

    recommendation: Mapped[Recommendation] = relationship(back_populates="daily_performance")


class StockDailyMaintenance(Base):
    __tablename__ = "stock_daily_maintenance"
    __table_args__ = (
        UniqueConstraint("stock_code", "market_date", name="uq_stock_daily_maintenance_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    market_date: Mapped[date] = mapped_column(Date, index=True)
    reference_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    latest_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    change_amount: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    change_percent: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    average_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    high_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    low_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    volume_lot: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    amount: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    bar_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tick_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    buy_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sell_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    buy_volume_lot: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    sell_volume_lot: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    latest_bar_timestamp: Mapped[str] = mapped_column(String(32), default="", server_default="")
    latest_tick_timestamp: Mapped[str] = mapped_column(String(32), default="", server_default="")
    reference_source: Mapped[str] = mapped_column(String(32), default="", server_default="")
    data_source: Mapped[str] = mapped_column(String(32), default="", server_default="")
    summary_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AlertSubscription(Base):
    __tablename__ = "alert_subscriptions"
    __table_args__ = (
        UniqueConstraint("stock_code", "subscriber", name="uq_alert_subscriptions_stock_subscriber"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    subscriber: Mapped[str] = mapped_column(String(64), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsDiscoveryCandidate(Base):
    __tablename__ = "news_discovery_candidates"
    __table_args__ = (
        UniqueConstraint("stock_code", "headline", "source_url", name="uq_news_candidate_unique"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    headline: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    source_site: Mapped[str] = mapped_column(String(255), default="", server_default="")
    source_url: Mapped[str] = mapped_column(String(500), default="", server_default="")
    event_type: Mapped[str] = mapped_column(String(64), default="generic", server_default="generic")
    discovery_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), default="candidate", server_default="candidate")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    promoted_recommendation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True
    )


class StockPrediction(Base):
    __tablename__ = "stock_predictions"
    __table_args__ = (
        UniqueConstraint("stock_code", "prediction_date", name="uq_stock_prediction_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    prediction_date: Mapped[date] = mapped_column(Date, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    direction: Mapped[str] = mapped_column(String(16), default="sideways", server_default="sideways")
    confidence: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    thesis: Mapped[str] = mapped_column(Text, default="", server_default="")
    invalidation_conditions: Mapped[str] = mapped_column(Text, default="", server_default="")
    risk_flags: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    evidence: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    predicted_by: Mapped[str] = mapped_column(String(64), default="llm", server_default="llm")
    actual_pnl_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_result: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    review_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IntradayBarRecord(Base):
    __tablename__ = "intraday_bars"
    __table_args__ = (
        UniqueConstraint("stock_code", "period", "adjust", "timestamp", name="uq_intraday_bar_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    period: Mapped[str] = mapped_column(String(8), default="1", server_default="1", index=True)
    adjust: Mapped[str] = mapped_column(String(8), default="", server_default="", index=True)
    timestamp: Mapped[str] = mapped_column(String(32), index=True)
    trading_day: Mapped[str] = mapped_column(String(10), default="", server_default="", index=True)
    open_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    close_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    high_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    low_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    volume: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    amount: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    amplitude: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    change_percent: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    change_amount: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    turnover_rate: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    source: Mapped[str] = mapped_column(String(32), default="akshare", server_default="akshare")
    used_cache: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IntradayTradeTick(Base):
    __tablename__ = "intraday_ticks"
    __table_args__ = (
        UniqueConstraint("stock_code", "trading_day", "row_index", name="uq_intraday_tick_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    trading_day: Mapped[str] = mapped_column(String(10), default="", server_default="", index=True)
    row_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    timestamp: Mapped[str] = mapped_column(String(16), index=True)
    price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    volume_lot: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    side: Mapped[str] = mapped_column(String(16), default="", server_default="")
    source: Mapped[str] = mapped_column(String(32), default="akshare", server_default="akshare")
    used_cache: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
