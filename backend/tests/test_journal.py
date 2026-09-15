import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.core.config import get_settings
from app.api import journal_api
from app.core.json_store import write_json

app = create_app()


@pytest.fixture(autouse=True)
def clean_journal(tmp_path, monkeypatch):
    test_store = tmp_path / "test_journal_store.json"
    write_json(test_store, {})
    monkeypatch.setattr(journal_api, "JOURNAL_STORE_FILE", test_store)
    monkeypatch.setattr(journal_api, "_journal", {})


@pytest.mark.anyio
async def test_journal_crud_and_store_persistence():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create first journal entry
        entry1_payload = {
            "symbol": "BTC/USDT",
            "direction": "long",
            "entry": 50000.0,
            "stop_loss": 49000.0,
            "take_profit": 52000.0,
            "notes": "First setup",
            "confluence_score": 85,
        }
        r1 = await client.post("/api/v1/journal/entries", json=entry1_payload, headers=headers)
        assert r1.status_code == 200
        e1 = r1.json()
        assert e1["id"]
        assert e1["symbol"] == "BTC/USDT"

        # 2. Create second journal entry (MUST NOT overwrite entry1!)
        entry2_payload = {
            "symbol": "ETH/USDT",
            "direction": "short",
            "entry": 3000.0,
            "stop_loss": 3100.0,
            "take_profit": 2800.0,
            "notes": "Second setup",
            "confluence_score": 75,
        }
        r2 = await client.post("/api/v1/journal/entries", json=entry2_payload, headers=headers)
        assert r2.status_code == 200
        e2 = r2.json()
        assert e2["id"] != e1["id"]

        # 3. List entries - both must exist
        r_list = await client.get("/api/v1/journal/entries", headers=headers)
        assert r_list.status_code == 200
        data = r_list.json()
        assert data["total"] == 2
        entry_ids = {item["id"] for item in data["entries"]}
        assert e1["id"] in entry_ids
        assert e2["id"] in entry_ids

        # 4. Update entry1
        update_payload = {
            "outcome": "win",
            "close_price": 52000.0,
            "pnl": 2000.0,
            "pnl_pct": 4.0,
            "notes": "Hit full TP",
        }
        r_up = await client.patch(f"/api/v1/journal/entries/{e1['id']}", json=update_payload, headers=headers)
        assert r_up.status_code == 200
        up_data = r_up.json()
        assert up_data["outcome"] == "win"
        assert up_data["pnl"] == 2000.0

        # Verify entry2 was NOT affected by entry1 update
        r_e2 = await client.get(f"/api/v1/journal/entries/{e2['id']}", headers=headers)
        assert r_e2.status_code == 200
        assert r_e2.json()["symbol"] == "ETH/USDT"
        assert r_e2.json()["outcome"] is None

        # 5. Check stats
        r_stats = await client.get("/api/v1/journal/stats", headers=headers)
        assert r_stats.status_code == 200
        stats = r_stats.json()
        assert stats["total"] == 2
        assert stats["total_pnl"] == 2000.0

        # 6. Delete entry1
        r_del = await client.delete(f"/api/v1/journal/entries/{e1['id']}", headers=headers)
        assert r_del.status_code == 200

        # Verify only entry2 remains
        r_list_after = await client.get("/api/v1/journal/entries", headers=headers)
        assert r_list_after.json()["total"] == 1
        assert r_list_after.json()["entries"][0]["id"] == e2["id"]
