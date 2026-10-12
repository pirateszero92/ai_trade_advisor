import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.decision_trace import DecisionTraceService


@pytest.mark.anyio
async def test_decision_trace_is_append_only_and_deduplicates_identical_facts(tmp_path):
    path = tmp_path / "decision_traces.json"
    service = DecisionTraceService(path)

    await service.record(
        snapshot_id="snap-1",
        event_id="event-1",
        symbol="BTC/USDT",
        stage="SNAPSHOT",
        facts={
            "config_hash": "cfg",
            "candle_digest": "candles",
            "unknown": None,
        },
    )
    await service.record(
        snapshot_id="snap-1",
        event_id="event-1",
        symbol="BTC/USDT",
        stage="SNAPSHOT",
        facts={
            "config_hash": "cfg",
            "candle_digest": "candles",
            "unknown": None,
        },
    )
    await service.record(
        snapshot_id="snap-1",
        event_id="event-1",
        symbol="BTC/USDT",
        stage="AI_REVIEW",
        facts={"status": "reviewed", "verdict": "COHERENT"},
    )

    record = await service.get(snapshot_id="snap-1", event_id="event-1")
    assert len(record["events"]) == 2
    assert "unknown" not in record["events"][0]["facts"]
    assert service.verify(record) == []
@pytest.mark.anyio
async def test_decision_trace_survives_restart(tmp_path):
    path = tmp_path / "decision_traces.json"
    first = DecisionTraceService(path)
    await first.record(
        snapshot_id="snap-1",
        event_id="event-1",
        symbol="BTC/USDT",
        stage="RISK",
        facts={"approved": True, "risk_amount": 100.0, "position_size": 2.0},
    )

    second = DecisionTraceService(path)
    record = await second.get(snapshot_id="snap-1", event_id="event-1")

    assert record is not None
    assert record["events"][0]["stage"] == "RISK"
    assert record["events"][0]["facts"]["risk_amount"] == 100.0
    assert second.verify(record) == []


@pytest.mark.anyio
async def test_decision_trace_integrity_detects_tampering(tmp_path):
    service = DecisionTraceService(tmp_path / "decision_traces.json")
    await service.record(
        snapshot_id="snap-1",
        event_id="event-1",
        symbol="BTC/USDT",
        stage="OMS_RESULT",
        facts={"status": "open", "fill_price": 100.5},
    )
    record = await service.get(snapshot_id="snap-1", event_id="event-1")
    record["events"][0]["facts"]["fill_price"] = 999.0

    failures = service.verify(record)

    assert any("fact_hash_mismatch" in failure for failure in failures)
    assert any("trace_hash_mismatch" in failure for failure in failures)


@pytest.mark.anyio
async def test_decision_trace_disk_payload_contains_candidate_identity(tmp_path):
    path = tmp_path / "decision_traces.json"
    service = DecisionTraceService(path)
    await service.record(
        snapshot_id="snap-abc",
        event_id="event-xyz",
        symbol="MSFT",
        stage="EXECUTION_REVALIDATION",
        facts={"passed": True, "executable_rr": 2.4},
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload["snap-abc:event-xyz"]

    assert record["candidate_id"] == "snap-abc:event-xyz"
    assert record["snapshot_id"] == "snap-abc"
    assert record["event_id"] == "event-xyz"
    assert record["symbol"] == "MSFT"


@pytest.mark.anyio
async def test_hrd04_decision_trace_hot_store_is_bounded_and_archives_old_records(tmp_path):
    path = tmp_path / "decision_traces.json"
    archive_dir = tmp_path / "archive"
    service = DecisionTraceService(
        path,
        archive_dir=archive_dir,
        hot_record_limit=2,
    )

    for index in range(3):
        await service.record(
            snapshot_id=f"snap-{index}",
            event_id=f"event-{index}",
            symbol="BTC/USDT",
            stage="SNAPSHOT",
            facts={"index": index},
            occurred_at=f"2026-09-22T00:00:0{index}+00:00",
        )

    hot_payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(hot_payload) == 2
    assert "snap-0:event-0" not in hot_payload

    archives = list(archive_dir.glob("decision-traces-*.ndjson"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8").count("\n") == 1

    restarted = DecisionTraceService(
        path,
        archive_dir=archive_dir,
        hot_record_limit=2,
    )
    archived = await restarted.get(snapshot_id="snap-0", event_id="event-0")
    assert archived is not None
    assert archived["events"][0]["facts"]["index"] == 0
    assert restarted.verify(archived) == []

    all_records = await restarted.list_records()
    assert len(all_records) == 3


@pytest.mark.anyio
async def test_hrd04_archived_candidate_can_receive_late_append_without_losing_history(tmp_path):
    path = tmp_path / "decision_traces.json"
    archive_dir = tmp_path / "archive"
    service = DecisionTraceService(
        path,
        archive_dir=archive_dir,
        hot_record_limit=1,
    )

    await service.record(
        snapshot_id="snap-old",
        event_id="event-old",
        symbol="BTC/USDT",
        stage="SNAPSHOT",
        facts={"config_hash": "cfg"},
        occurred_at="2026-09-22T00:00:00+00:00",
    )
    await service.record(
        snapshot_id="snap-new",
        event_id="event-new",
        symbol="ETH/USDT",
        stage="SNAPSHOT",
        facts={"config_hash": "cfg2"},
        occurred_at="2026-09-22T00:01:00+00:00",
    )

    updated = await service.record(
        snapshot_id="snap-old",
        event_id="event-old",
        symbol="BTC/USDT",
        stage="AI_REVIEW",
        facts={"status": "reviewed", "verdict": "COHERENT"},
        occurred_at="2026-09-22T00:02:00+00:00",
    )

    assert [event["stage"] for event in updated["events"]] == [
        "SNAPSHOT",
        "AI_REVIEW",
    ]
    assert service.verify(updated) == []

    restarted = DecisionTraceService(
        path,
        archive_dir=archive_dir,
        hot_record_limit=1,
    )
    restored = await restarted.get(snapshot_id="snap-old", event_id="event-old")
    assert restored is not None
    assert len(restored["events"]) == 2
    assert restarted.verify(restored) == []


@pytest.mark.anyio
async def test_hrd04_decision_trace_archive_retention_purges_expired_daily_files(tmp_path):
    path = tmp_path / "decision_traces.json"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    old_date = (datetime.now(timezone.utc) - timedelta(days=400)).date().isoformat()
    old_archive = archive_dir / f"decision-traces-{old_date}.ndjson"
    old_archive.write_text("{}\n", encoding="utf-8")

    service = DecisionTraceService(
        path,
        archive_dir=archive_dir,
        archive_retention_days=365,
    )
    await service.list_records()

    assert not old_archive.exists()
