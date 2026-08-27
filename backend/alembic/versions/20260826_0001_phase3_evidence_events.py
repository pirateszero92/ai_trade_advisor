"""Create immutable Phase 3 evidence events.

Revision ID: 20260826_0001
Revises:
Create Date: 2026-08-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260826_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evidence_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False),
        sa.Column("market_type", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("engine_version", sa.String(length=30), nullable=False),
        sa.Column("strategy_version", sa.String(length=30), nullable=False),
        sa.Column("indicator_version", sa.Integer(), nullable=False),
        sa.Column("regime_version", sa.Integer(), nullable=False),
        sa.Column("replayable", sa.Boolean(), nullable=False),
        sa.Column("market_data_hash", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_events_occurred_at", "evidence_events", ["occurred_at"])
    op.create_index("ix_evidence_events_recorded_at", "evidence_events", ["recorded_at"])
    op.create_index("ix_evidence_events_symbol", "evidence_events", ["symbol"])
    op.create_index("ix_evidence_events_timeframe", "evidence_events", ["timeframe"])
    op.create_index("ix_evidence_events_decision_hash", "evidence_events", ["decision_hash"])
    op.create_index("ix_evidence_symbol_time", "evidence_events", ["symbol", "occurred_at"])
    op.create_index("ix_evidence_source_time", "evidence_events", ["source", "occurred_at"])
    op.create_index("ix_evidence_strategy_time", "evidence_events", ["strategy_version", "occurred_at"])

    op.execute(
        """
        CREATE FUNCTION reject_evidence_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evidence_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER evidence_events_append_only
        BEFORE UPDATE OR DELETE ON evidence_events
        FOR EACH ROW EXECUTE FUNCTION reject_evidence_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evidence_events_append_only ON evidence_events")
    op.drop_table("evidence_events")
    op.execute("DROP FUNCTION IF EXISTS reject_evidence_event_mutation()")

