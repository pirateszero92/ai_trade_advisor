from copy import deepcopy

from app.engines.backtest_engine import (
    WalkForwardValidationCriteria,
    evaluate_walk_forward_validation,
)


def _run(
    *,
    symbol: str,
    asset_class: str,
    expectancy: float = 0.20,
    profit_factor: float = 1.40,
    completed: int = 60,
    folds: int = 3,
    coverage: float = 0.99,
    history_days: float = 180.0,
    fill_rate: float = 0.85,
    drawdown: float = 8.0,
    regimes=("trending", "ranging"),
):
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "evaluation_mode": "rolling_walk_forward_oos",
        "replay_manifest": {
            "future_leakage_allowed": False,
            "llm_called_during_backtest": False,
        },
        "metrics": {
            "completed_trades": completed,
            "expectancy_r": expectancy,
            "profit_factor": profit_factor,
            "max_drawdown_pct": drawdown,
            "fill_rate": fill_rate,
            "oos_fold_count": folds,
            "by_regime": {name: {"trades": 30} for name in regimes},
            "data_readiness": {
                "coverage": coverage,
                "history_days": history_days,
            },
            "tri_core_policy": {
                "calibration_status": "draft_unvalidated",
            },
        },
    }


def test_phase20_multi_symbol_multi_asset_validation_passes_without_auto_promotion():
    runs = [
        _run(symbol="BTC/USDT", asset_class="crypto"),
        _run(symbol="MSFT", asset_class="equity"),
    ]
    before = deepcopy(runs)

    result = evaluate_walk_forward_validation(
        runs,
        WalkForwardValidationCriteria(),
        current_policy_status="draft_unvalidated",
    )

    assert result["passed"] is True
    assert result["validation_candidate"] is True
    assert result["human_approval_required"] is True
    assert result["production_eligible"] is False
    assert result["config_mutated"] is False
    assert result["current_policy_status"] == "draft_unvalidated"
    assert result["next_status_requires_human_action"] == "walk_forward_validated"
    assert result["symbols"] == ["BTC/USDT", "MSFT"]
    assert result["asset_classes"] == ["crypto", "equity"]
    assert set(result["regimes"]) == {"trending", "ranging"}
    assert runs == before


def test_phase20_validation_fails_when_asset_class_coverage_is_insufficient():
    runs = [
        _run(symbol="BTC/USDT", asset_class="crypto"),
        _run(symbol="ETH/USDT", asset_class="crypto"),
    ]

    result = evaluate_walk_forward_validation(
        runs,
        WalkForwardValidationCriteria(),
    )

    assert result["passed"] is False
    assert result["aggregate_checks"]["asset_class_coverage"] is False
    assert "asset_class_coverage" in result["failure_reasons"]
    assert result["next_status_requires_human_action"] is None


def test_phase20_validation_fails_closed_when_one_symbol_has_weak_oos_evidence():
    runs = [
        _run(symbol="BTC/USDT", asset_class="crypto"),
        _run(
            symbol="MSFT",
            asset_class="equity",
            expectancy=-0.10,
            profit_factor=0.90,
            folds=2,
            coverage=0.80,
            history_days=30.0,
        ),
    ]

    result = evaluate_walk_forward_validation(
        runs,
        WalkForwardValidationCriteria(),
    )

    assert result["passed"] is False
    weak = next(item for item in result["per_symbol"] if item["symbol"] == "MSFT")
    assert weak["passed"] is False
    assert weak["checks"]["expectancy_r"] is False
    assert weak["checks"]["profit_factor"] is False
    assert weak["checks"]["oos_folds"] is False
    assert weak["checks"]["true_aggressor_coverage"] is False
    assert weak["checks"]["history_days"] is False
    assert result["aggregate_checks"]["all_symbol_runs_pass"] is False


def test_phase20_gate_only_assesses_draft_policy_and_never_repromotes():
    runs = [
        _run(symbol="BTC/USDT", asset_class="crypto"),
        _run(symbol="MSFT", asset_class="equity"),
    ]

    result = evaluate_walk_forward_validation(
        runs,
        WalkForwardValidationCriteria(),
        current_policy_status="walk_forward_validated",
    )

    assert result["passed"] is False
    assert result["aggregate_checks"]["policy_still_draft"] is False
    assert result["production_eligible"] is False
    assert result["config_mutated"] is False
