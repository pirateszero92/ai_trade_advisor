"""Add durable Paper OMS rolling-risk halts.

Revision ID: 20260907_0005
Revises: 20260827_0004
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260907_0005"
down_revision: Union[str, None] = "20260827_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_oms_risk_halts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("halted_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_oms_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_oms_risk_halts_account_id", "paper_oms_risk_halts", ["account_id"])
    op.create_index("ix_paper_oms_risk_halts_halted_until", "paper_oms_risk_halts", ["halted_until"])
    op.create_index(
        "ix_paper_oms_risk_halt_account_until",
        "paper_oms_risk_halts",
        ["account_id", "halted_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_oms_risk_halt_account_until", table_name="paper_oms_risk_halts")
    op.drop_index("ix_paper_oms_risk_halts_halted_until", table_name="paper_oms_risk_halts")
    op.drop_index("ix_paper_oms_risk_halts_account_id", table_name="paper_oms_risk_halts")
    op.drop_table("paper_oms_risk_halts")
