"""Create Phase 3 normalized ledgers, backtests and release gates.

Revision ID: 20260826_0002
Revises: 20260826_0001
Create Date: 2026-08-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260826_0002"
down_revision: Union[str, None] = "20260826_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trade_ledger_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("broker", sa.String(length=30), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("requested_quantity", sa.Float(), nullable=True),
        sa.Column("filled_quantity", sa.Float(), nullable=True),
        sa.Column("close_price", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("mfe", sa.Float(), nullable=True),
        sa.Column("mae", sa.Float(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mirrored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_ledger_records_mode", "trade_ledger_records", ["mode"])
    op.create_index("ix_trade_ledger_records_symbol", "trade_ledger_records", ["symbol"])
    op.create_index("ix_trade_ledger_records_status", "trade_ledger_records", ["status"])
    op.create_index("ix_trade_ledger_mode_status", "trade_ledger_records", ["mode", "status"])
    op.create_index("ix_trade_ledger_symbol_opened", "trade_ledger_records", ["symbol", "opened_at"])

    op.create_table(
        "order_ledger_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_id", sa.String(length=64), nullable=False),
        sa.Column("leg", sa.String(length=12), nullable=False),
        sa.Column("client_order_id", sa.String(length=100), nullable=True),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("requested_quantity", sa.Float(), nullable=False),
        sa.Column("filled_quantity", sa.Float(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["trade_id"], ["trade_ledger_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_id", "leg", name="uq_order_ledger_trade_leg"),
    )
    op.create_index("ix_order_ledger_records_trade_id", "order_ledger_records", ["trade_id"])
    op.create_index("ix_order_ledger_records_client_order_id", "order_ledger_records", ["client_order_id"])
    op.create_index("ix_order_ledger_records_status", "order_ledger_records", ["status"])

    op.create_table(
        "fill_ledger_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_id", sa.String(length=64), nullable=False),
        sa.Column("leg", sa.String(length=12), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("spread_cost", sa.Float(), nullable=False),
        sa.Column("slippage_cost", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("liquidity", sa.String(length=12), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["order_ledger_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trade_id"], ["trade_ledger_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fill_ledger_records_order_id", "fill_ledger_records", ["order_id"])
    op.create_index("ix_fill_ledger_records_trade_id", "fill_ledger_records", ["trade_id"])
    op.create_index("ix_fill_ledger_records_filled_at", "fill_ledger_records", ["filled_at"])

    op.create_table(
        "json_migration_checkpoints",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=True),
        sa.Column("timeframe", sa.String(length=10), nullable=True),
        sa.Column("strategy_version", sa.String(length=30), nullable=False),
        sa.Column("evaluation_mode", sa.String(length=30), nullable=False),
        sa.Column("data_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])
    op.create_index("ix_backtest_runs_run_type", "backtest_runs", ["run_type"])
    op.create_index("ix_backtest_runs_symbol", "backtest_runs", ["symbol"])
    op.create_index("ix_backtest_runs_strategy_version", "backtest_runs", ["strategy_version"])

    op.create_table(
        "release_gate_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("human_approval_required", sa.Boolean(), nullable=False),
        sa.Column("production_eligible", sa.Boolean(), nullable=False),
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("failure_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["backtest_run_id"], ["backtest_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_release_gate_evaluations_backtest_run_id", "release_gate_evaluations", ["backtest_run_id"])

    op.execute(
        """
        CREATE FUNCTION reject_phase3_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Phase 3 result tables are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("fill_ledger_records", "backtest_runs", "release_gate_evaluations"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_phase3_result_mutation();
            """
        )


def downgrade() -> None:
    for table in ("release_gate_evaluations", "backtest_runs", "fill_ledger_records"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.drop_table("release_gate_evaluations")
    op.drop_table("backtest_runs")
    op.drop_table("json_migration_checkpoints")
    op.drop_table("fill_ledger_records")
    op.drop_table("order_ledger_records")
    op.drop_table("trade_ledger_records")
    op.execute("DROP FUNCTION IF EXISTS reject_phase3_result_mutation()")

