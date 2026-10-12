import json

import pytest

from app.api.settings_api import RuntimeSettingsUpdateRequest, update_runtime_settings
from app.core import runtime_config
from app.core.config_contract import (
    CONFIG_CONTRACT_VERSION,
    config_manifest,
    runtime_sections,
    validate_runtime_config,
)
from app.services import event_trigger


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_phase24_runtime_sections_have_single_declared_owner():
    data = {
        "entry_mode": "limit",
        "target_rr": 2.0,
        "risk_per_trade": 1.0,
        "auto_trade_require_ai_approval": True,
        "watchlist": [],
    }
    sections = runtime_sections(data)

    assert sections["execution"]["entry_mode"] == "limit"
    assert sections["execution"]["target_rr"] == 2.0
    assert sections["risk"]["risk_per_trade"] == 1.0
    assert sections["ai"]["auto_trade_require_ai_approval"] is True
    assert sections["research"]["watchlist"] == []


def test_phase24_runtime_validation_rejects_mtf_hard_gate_and_bad_rr():
    with pytest.raises(ValueError, match="context-only"):
        validate_runtime_config({"mtf_hierarchy_required": True})

    with pytest.raises(ValueError, match="target_rr"):
        validate_runtime_config({"target_rr": 0.5})


def test_phase24_manifest_exposes_hashes_aliases_and_authority_notes():
    runtime = {
        "entry_mode": "limit",
        "target_rr": 2.0,
        "risk_per_trade": 1.0,
        "mtf_hierarchy_required": False,
    }
    manifest = config_manifest(
        strategy={"tri_core_policy": {"minimum_rr": 2.0}},
        runtime=runtime,
        runtime_revision=7,
    )

    assert manifest["contract_version"] == CONFIG_CONTRACT_VERSION
    assert manifest["runtime_revision"] == 7
    assert len(manifest["strategy_hash"]) == 64
    assert len(manifest["runtime_hash"]) == 64
    assert manifest["legacy_runtime_aliases"]["target_rr"]["canonical_name"] == "minimum_executable_rr"
    assert manifest["authority_notes"]["position_sizing"] == "RiskEngine only"
    assert "filters" in manifest["strategy_ownership"]
    assert manifest["strategy_ownership"]["filters"] == "legacy_compatibility"


def test_phase24_revision_invalidates_runtime_cache_without_ttl_wait(tmp_path, monkeypatch):
    runtime_file = tmp_path / "runtime.json"
    _write(runtime_file, {"entry_mode": "limit", "target_rr": 2.0, "mtf_hierarchy_required": False})
    monkeypatch.setattr(runtime_config, "RUNTIME_SETTINGS_FILE", runtime_file)
    monkeypatch.setattr(runtime_config, "_RUNTIME_REVISION", 100)
    event_trigger.invalidate_runtime_settings_cache()

    first = event_trigger._get_cached_runtime_settings()
    assert first["target_rr"] == 2.0

    runtime_config.update_runtime_config({"target_rr": 2.5})
    second = event_trigger._get_cached_runtime_settings()

    assert second["target_rr"] == 2.5
    assert runtime_config.runtime_config_revision() == 101


@pytest.mark.anyio
async def test_phase24_runtime_fast_path_maps_mobile_safety_toggles(tmp_path, monkeypatch):
    runtime_file = tmp_path / "runtime.json"
    _write(runtime_file, {
        "auto_sl_tp": True,
        "auto_invalidation": True,
        "mtf_hierarchy_required": False,
    })
    monkeypatch.setattr(runtime_config, "RUNTIME_SETTINGS_FILE", runtime_file)
    monkeypatch.setattr(runtime_config, "_RUNTIME_REVISION", 200)

    result = await update_runtime_settings(
        RuntimeSettingsUpdateRequest(
            auto_sl_tp=False,
            auto_invalidation=False,
        ),
        _key="test",
    )

    saved = runtime_config.load_runtime_config()
    assert saved["auto_sl_tp"] is False
    assert saved["auto_invalidation"] is False
    assert result["config"]["auto_sl_tp"] is False
    assert result["config_contract"]["runtime_revision"] == 201
    assert result["config_contract"]["field_ownership"]["auto_sl_tp"] == "execution"


def test_phase24_current_runtime_config_has_no_unclassified_fields():
    current = runtime_config.load_runtime_config()
    assert runtime_sections(current)["unclassified"] == {}


def test_phase24_rr_thresholds_have_distinct_authority_names():
    manifest = config_manifest(
        strategy={"tri_core_policy": {"minimum_rr": 2.0}},
        runtime={"target_rr": 2.0, "mtf_hierarchy_required": False},
        runtime_revision=1,
    )

    assert manifest["authority_notes"]["structural_rr"] == "tri_core_policy.minimum_rr"
    assert "minimum_executable_rr" in manifest["authority_notes"]["executable_rr"]


@pytest.mark.anyio
async def test_phase24_config_contract_endpoint_exposes_grouped_runtime(tmp_path, monkeypatch):
    from app.api.settings_api import get_config_contract

    runtime_file = tmp_path / "runtime.json"
    _write(runtime_file, {
        "entry_mode": "limit",
        "target_rr": 2.0,
        "risk_per_trade": 1.0,
        "mtf_hierarchy_required": False,
    })
    monkeypatch.setattr(runtime_config, "RUNTIME_SETTINGS_FILE", runtime_file)
    monkeypatch.setattr(runtime_config, "_RUNTIME_REVISION", 300)

    result = await get_config_contract(_key="test")

    assert result["contract_version"] == CONFIG_CONTRACT_VERSION
    assert result["runtime_revision"] == 300
    assert result["runtime_sections"]["execution"]["target_rr"] == 2.0
    assert result["runtime_sections"]["risk"]["risk_per_trade"] == 1.0


@pytest.mark.anyio
async def test_phase24_risk_config_exposes_canonical_aliases(tmp_path, monkeypatch):
    from app.api.settings_api import get_risk_config

    runtime_file = tmp_path / "runtime.json"
    _write(runtime_file, {
        "entry_mode": "limit",
        "target_rr": 2.25,
        "default_sl_pct": 1.5,
        "mtf_hierarchy_required": False,
    })
    monkeypatch.setattr(runtime_config, "RUNTIME_SETTINGS_FILE", runtime_file)
    monkeypatch.setattr(runtime_config, "_RUNTIME_REVISION", 400)

    result = await get_risk_config(_key="test")

    assert result["target_rr"] == 2.25
    assert result["minimum_executable_rr"] == 2.25
    assert result["default_sl_pct"] == 1.5
    assert result["max_stop_distance_pct"] == 1.5
    assert result["config_contract"]["field_ownership"]["target_rr"] == "execution"
