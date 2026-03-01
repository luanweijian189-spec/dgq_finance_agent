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
