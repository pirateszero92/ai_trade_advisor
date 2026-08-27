"""Persist Paper OMS breakeven and trailing-stop state.

Revision ID: 20260827_0004
Revises: 20260827_0003
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260827_0004"
down_revision: Union[str, None] = "20260827_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MONEY = sa.Numeric(precision=28, scale=10)


def upgrade() -> None:
    op.add_column("paper_oms_positions", sa.Column("favorable_extreme", MONEY, nullable=True))
    op.add_column(
        "paper_oms_positions",
        sa.Column("max_r_multiple", MONEY, nullable=False, server_default="0"),
    )
    op.add_column(
        "paper_oms_positions",
        sa.Column("protection_stage", sa.String(length=32), nullable=False, server_default="initial"),
    )
    op.add_column(
        "paper_oms_positions",
        sa.Column("protection_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paper_oms_positions", "protection_updated_at")
    op.drop_column("paper_oms_positions", "protection_stage")
    op.drop_column("paper_oms_positions", "max_r_multiple")
    op.drop_column("paper_oms_positions", "favorable_extreme")
