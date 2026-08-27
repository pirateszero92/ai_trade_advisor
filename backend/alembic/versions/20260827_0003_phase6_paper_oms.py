"""Create the authoritative Phase 6 Paper OMS.

Revision ID: 20260827_0003
Revises: 20260826_0002
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260827_0003"
down_revision: Union[str, None] = "20260826_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MONEY = sa.Numeric(precision=28, scale=10)


def upgrade() -> None:
    op.create_table(
        "paper_oms_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("initial_capital", MONEY, nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_oms_accounts_active", "paper_oms_accounts", ["active"])
    op.create_index(
        "uq_paper_oms_single_active_account",
        "paper_oms_accounts",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active IS TRUE"),
    )

    op.create_table(
        "paper_oms_positions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("tag", sa.String(length=100), nullable=False),
        sa.Column("notes", sa.String(length=4000), nullable=False),
        sa.Column("requested_quantity", MONEY, nullable=False),
        sa.Column("opened_quantity", MONEY, nullable=False),
        sa.Column("closed_quantity", MONEY, nullable=False),
        sa.Column("remaining_quantity", MONEY, nullable=False),
        sa.Column("requested_entry_price", MONEY, nullable=False),
        sa.Column("average_entry_price", MONEY, nullable=True),
        sa.Column("average_exit_price", MONEY, nullable=True),
        sa.Column("stop_loss", MONEY, nullable=False),
        sa.Column("initial_stop_loss", MONEY, nullable=False),
        sa.Column("take_profit", MONEY, nullable=False),
        sa.Column("realized_pnl_gross", MONEY, nullable=False),
        sa.Column("realized_pnl_net", MONEY, nullable=False),
        sa.Column("fees_total", MONEY, nullable=False),
        sa.Column("spread_cost_total", MONEY, nullable=False),
        sa.Column("slippage_cost_total", MONEY, nullable=False),
        sa.Column("risk_pct", MONEY, nullable=False),
        sa.Column("auto_be", sa.Boolean(), nullable=False),
        sa.Column("trailing_stop", sa.Boolean(), nullable=False),
        sa.Column("close_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_oms_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_oms_positions_account_id", "paper_oms_positions", ["account_id"])
    op.create_index("ix_paper_oms_positions_symbol", "paper_oms_positions", ["symbol"])
    op.create_index("ix_paper_oms_positions_status", "paper_oms_positions", ["status"])
    op.create_index("ix_paper_oms_position_account_status", "paper_oms_positions", ["account_id", "status"])
    op.create_index("ix_paper_oms_position_symbol_status", "paper_oms_positions", ["symbol", "status"])

    op.create_table(
        "paper_oms_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", sa.String(length=64), nullable=False),
        sa.Column("client_order_id", sa.String(length=100), nullable=False),
        sa.Column("leg", sa.String(length=12), nullable=False),
        sa.Column("position_effect", sa.String(length=12), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_quantity", MONEY, nullable=False),
        sa.Column("filled_quantity", MONEY, nullable=False),
        sa.Column("remaining_quantity", MONEY, nullable=False),
        sa.Column("limit_price", MONEY, nullable=True),
        sa.Column("average_fill_price", MONEY, nullable=True),
        sa.Column("close_reason", sa.String(length=200), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_oms_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["position_id"], ["paper_oms_positions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "client_order_id", name="uq_paper_oms_account_client_order"),
    )
    op.create_index("ix_paper_oms_orders_account_id", "paper_oms_orders", ["account_id"])
    op.create_index("ix_paper_oms_orders_position_id", "paper_oms_orders", ["position_id"])
    op.create_index("ix_paper_oms_orders_status", "paper_oms_orders", ["status"])
    op.create_index("ix_paper_oms_order_position_status", "paper_oms_orders", ["position_id", "status"])

    op.create_table(
        "paper_oms_fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", sa.String(length=64), nullable=False),
        sa.Column("execution_key", sa.String(length=180), nullable=False),
        sa.Column("leg", sa.String(length=12), nullable=False),
        sa.Column("position_effect", sa.String(length=12), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("reference_price", MONEY, nullable=False),
        sa.Column("fill_price", MONEY, nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("fee", MONEY, nullable=False),
        sa.Column("spread_cost", MONEY, nullable=False),
        sa.Column("slippage_cost", MONEY, nullable=False),
        sa.Column("liquidity", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_sequence", sa.String(length=80), nullable=True),
        sa.Column("exchange_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_oms_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["paper_oms_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["position_id"], ["paper_oms_positions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_key"),
    )
    op.create_index("ix_paper_oms_fills_account_id", "paper_oms_fills", ["account_id"])
    op.create_index("ix_paper_oms_fills_order_id", "paper_oms_fills", ["order_id"])
    op.create_index("ix_paper_oms_fills_position_id", "paper_oms_fills", ["position_id"])
    op.create_index("ix_paper_oms_fills_filled_at", "paper_oms_fills", ["filled_at"])

    op.create_table(
        "paper_oms_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", sa.String(length=64), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_key", sa.String(length=180), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("previous_status", sa.String(length=24), nullable=True),
        sa.Column("new_status", sa.String(length=24), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_oms_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["paper_oms_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["position_id"], ["paper_oms_positions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index("ix_paper_oms_events_account_id", "paper_oms_events", ["account_id"])
    op.create_index("ix_paper_oms_events_position_id", "paper_oms_events", ["position_id"])
    op.create_index("ix_paper_oms_events_order_id", "paper_oms_events", ["order_id"])
    op.create_index("ix_paper_oms_events_event_type", "paper_oms_events", ["event_type"])
    op.create_index("ix_paper_oms_events_occurred_at", "paper_oms_events", ["occurred_at"])

    op.execute(
        """
        CREATE FUNCTION reject_paper_oms_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Paper OMS fills and events are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("paper_oms_fills", "paper_oms_events"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_paper_oms_audit_mutation();
            """
        )


def downgrade() -> None:
    for table in ("paper_oms_events", "paper_oms_fills"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.drop_table("paper_oms_events")
    op.drop_table("paper_oms_fills")
    op.drop_table("paper_oms_orders")
    op.drop_table("paper_oms_positions")
    op.drop_table("paper_oms_accounts")
    op.execute("DROP FUNCTION IF EXISTS reject_paper_oms_audit_mutation()")
