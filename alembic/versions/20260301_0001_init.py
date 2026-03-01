"""init tables

Revision ID: 20260301_0001
Revises:
Create Date: 2026-03-01 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260301_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommenders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("wechat_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("reliability_score", sa.Float(), nullable=False, server_default="50"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recommenders_name"), "recommenders", ["name"], unique=False)

    op.create_table(
        "stocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("industry", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_code", name="uq_stocks_stock_code"),
    )
    op.create_index(op.f("ix_stocks_stock_code"), "stocks", ["stock_code"], unique=False)

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("recommender_id", sa.Integer(), nullable=False),
        sa.Column("recommend_ts", sa.DateTime(), nullable=False),
        sa.Column("initial_price", sa.Float(), nullable=True),
        sa.Column("original_message", sa.Text(), nullable=False),
        sa.Column("extracted_logic", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="tracking"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="wechat"),
        sa.ForeignKeyConstraint(["recommender_id"], ["recommenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recommendations_recommend_ts"), "recommendations", ["recommend_ts"], unique=False)
    op.create_index(op.f("ix_recommendations_recommender_id"), "recommendations", ["recommender_id"], unique=False)
    op.create_index(op.f("ix_recommendations_stock_id"), "recommendations", ["stock_id"], unique=False)

    op.create_table(
        "daily_performance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("close_price", sa.Float(), nullable=False),
        sa.Column("high_price", sa.Float(), nullable=False),
        sa.Column("low_price", sa.Float(), nullable=False),
        sa.Column("pnl_percent", sa.Float(), nullable=False),
        sa.Column("max_drawdown", sa.Float(), nullable=False),
        sa.Column("evaluation_score", sa.Float(), nullable=False),
        sa.Column("sharpe_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("logic_validated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("market_cap_score", sa.Float(), nullable=False, server_default="50"),
        sa.Column("elasticity_score", sa.Float(), nullable=False, server_default="50"),
        sa.Column("liquidity_score", sa.Float(), nullable=False, server_default="50"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recommendation_id", "date", name="uq_daily_performance_reco_date"),
    )
    op.create_index(op.f("ix_daily_performance_date"), "daily_performance", ["date"], unique=False)
    op.create_index(op.f("ix_daily_performance_recommendation_id"), "daily_performance", ["recommendation_id"], unique=False)

    op.create_table(
        "alert_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("subscriber", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_code", "subscriber", name="uq_alert_subscriptions_stock_subscriber"),
    )
    op.create_index(op.f("ix_alert_subscriptions_stock_code"), "alert_subscriptions", ["stock_code"], unique=False)
    op.create_index(op.f("ix_alert_subscriptions_subscriber"), "alert_subscriptions", ["subscriber"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_alert_subscriptions_subscriber"), table_name="alert_subscriptions")
    op.drop_index(op.f("ix_alert_subscriptions_stock_code"), table_name="alert_subscriptions")
    op.drop_table("alert_subscriptions")

    op.drop_index(op.f("ix_daily_performance_recommendation_id"), table_name="daily_performance")
    op.drop_index(op.f("ix_daily_performance_date"), table_name="daily_performance")
    op.drop_table("daily_performance")

    op.drop_index(op.f("ix_recommendations_stock_id"), table_name="recommendations")
    op.drop_index(op.f("ix_recommendations_recommender_id"), table_name="recommendations")
    op.drop_index(op.f("ix_recommendations_recommend_ts"), table_name="recommendations")
    op.drop_table("recommendations")

    op.drop_index(op.f("ix_stocks_stock_code"), table_name="stocks")
    op.drop_table("stocks")

    op.drop_index(op.f("ix_recommenders_name"), table_name="recommenders")
    op.drop_table("recommenders")
