"""add news discovery candidates

Revision ID: 20260303_0002
Revises: 20260301_0001
Create Date: 2026-03-03 22:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260303_0002"
down_revision = "20260301_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_discovery_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("headline", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_site", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(length=64), nullable=False, server_default="generic"),
        sa.Column("discovery_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="candidate"),
        sa.Column("discovered_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("promoted_recommendation_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["promoted_recommendation_id"], ["recommendations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_code", "headline", "source_url", name="uq_news_candidate_unique"),
    )

    op.create_index(op.f("ix_news_discovery_candidates_stock_code"), "news_discovery_candidates", ["stock_code"], unique=False)
    op.create_index(op.f("ix_news_discovery_candidates_discovered_at"), "news_discovery_candidates", ["discovered_at"], unique=False)
    op.create_index(op.f("ix_news_discovery_candidates_last_seen_at"), "news_discovery_candidates", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_news_discovery_candidates_last_seen_at"), table_name="news_discovery_candidates")
    op.drop_index(op.f("ix_news_discovery_candidates_discovered_at"), table_name="news_discovery_candidates")
    op.drop_index(op.f("ix_news_discovery_candidates_stock_code"), table_name="news_discovery_candidates")
    op.drop_table("news_discovery_candidates")
