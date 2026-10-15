"""Phase 7 release-readiness checks for guarded paper execution."""

import json
from pathlib import Path

from app.core.config_contract import runtime_sections, validate_runtime_config


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILE = ROOT / "backend" / "config" / "runtime_settings.json"


def test_phase7_runtime_is_paper_and_guarded():
    runtime = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    assert runtime.get("auto_trade_ai_policy") == "guarded"
    assert runtime.get("execution_shadow_compare_enabled") is True


def test_phase7_runtime_has_no_unclassified_configuration():
    runtime = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    validated = validate_runtime_config(runtime)
    assert runtime_sections(validated)["unclassified"] == {}


def test_phase7_execution_safety_defaults_are_present():
    runtime = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    assert 1 <= int(runtime["auto_trade_limit_timeout_bars"]) <= 20
    assert runtime["auto_trade_entry_policy"] in {"fixed", "adaptive"}
    assert runtime["auto_trade_min_grade"] in {"A", "S"}
    assert float(runtime["target_rr"]) >= 1.0

