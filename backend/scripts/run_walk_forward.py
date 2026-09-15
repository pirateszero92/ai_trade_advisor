"""
Walk-Forward Validation Runner

Simulates (or executes) the out-of-sample walk-forward validation process.
If all release criteria are met, it updates the `calibration_status`
in `backend/config/strategy.yaml` to 'walk_forward_validated'.
"""
import os
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

STRATEGY_CONFIG_PATH = BACKEND_DIR / "config" / "strategy.yaml"

# Release Criteria Thresholds (Matching PHASE_3 / Evidence Replay requirements)
CRITERIA = {
    "completed_trades": 100,
    "trades_per_scenario": 20,
    "expectancy_min": 0.05,
    "profit_factor_min": 1.15,
    "max_drawdown_max": 12.0,
    "fill_rate_min": 0.70,
    "regimes_tested_min": 2,
    "oos_folds_min": 3,
}


def get_validation_results() -> dict:
    """
    Retrieve Out-Of-Sample (OOS) backtest results.
    
    TODO: Connect this to your actual Backtest/Monte Carlo Engine database.
    For demonstration, this returns mock passing metrics.
    """
    print("🔄 Fetching Out-Of-Sample (OOS) walk-forward results...")
    
    # Mock passing results
    return {
        "completed_trades": 145,
        "trades_per_scenario": 28,
        "expectancy": 0.085,
        "profit_factor": 1.32,
        "max_drawdown": 7.5,
        "fill_rate": 0.88,
        "regimes_tested": 3,
        "oos_folds": 5,
    }


def validate_metrics(results: dict) -> bool:
    """Check if the backtest results meet the release criteria."""
    print("\n📊 Validating metrics against Release Criteria:")
    checks = {
        "completed_trades": results["completed_trades"] >= CRITERIA["completed_trades"],
        "trades_per_scenario": results["trades_per_scenario"] >= CRITERIA["trades_per_scenario"],
        "expectancy": results["expectancy"] >= CRITERIA["expectancy_min"],
        "profit_factor": results["profit_factor"] >= CRITERIA["profit_factor_min"],
        "max_drawdown": results["max_drawdown"] <= CRITERIA["max_drawdown_max"],
        "fill_rate": results["fill_rate"] >= CRITERIA["fill_rate_min"],
        "regimes_tested": results["regimes_tested"] >= CRITERIA["regimes_tested_min"],
        "oos_folds": results["oos_folds"] >= CRITERIA["oos_folds_min"],
    }
    
    all_passed = True
    for metric, passed in checks.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {status} - {metric}: {results[metric]}")
        if not passed:
            all_passed = False
            
    return all_passed


def update_policy_status(is_passed: bool) -> None:
    """Audit policy status without promoting mock results to production."""
    print("\n🔒 INTEGRITY GUARD: Automatic production promotion is disabled.")
    print("   Production promotion requires verifiable BacktestRun records,")
    print("   exact config hash match, and explicit human approval.")
    if is_passed:
        print("   Evaluation PASSED criteria (SIMULATION ONLY - strategy.yaml remains unchanged).")
    else:
        print("   Evaluation FAILED criteria.")


def main():
    print("🚀 Starting Walk-Forward Validation Runner (Dry-Run / Audit Mode)...")
    results = get_validation_results()
    is_passed = validate_metrics(results)
    update_policy_status(is_passed)


if __name__ == "__main__":
    main()
