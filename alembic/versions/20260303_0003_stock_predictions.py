"""add stock predictions table

Revision ID: 20260303_0003
Revises: 20260303_0002
Create Date: 2026-03-03 23:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260303_0003"
down_revision = "20260303_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default="sideways"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("thesis", sa.Text(), nullable=False, server_default=""),
        sa.Column("invalidation_conditions", sa.Text(), nullable=False, server_default=""),
        sa.Column("risk_flags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("predicted_by", sa.String(length=64), nullable=False, server_default="llm"),
        sa.Column("actual_pnl_percent", sa.Float(), nullable=True),
        sa.Column("review_result", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("review_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_code", "prediction_date", name="uq_stock_prediction_date"),
    )

    op.create_index(op.f("ix_stock_predictions_stock_code"), "stock_predictions", ["stock_code"], unique=False)
    op.create_index(op.f("ix_stock_predictions_prediction_date"), "stock_predictions", ["prediction_date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_stock_predictions_prediction_date"), table_name="stock_predictions")
    op.drop_index(op.f("ix_stock_predictions_stock_code"), table_name="stock_predictions")
    op.drop_table("stock_predictions")
