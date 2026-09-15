"""Read-only smoke check against the local deployment; never submits an order.

Run from repository root: python backend/tests/verify_running_execution.py
Reads the local backend .env key without printing it.
"""
import asyncio
import json
from pathlib import Path

import httpx
import websockets
from dotenv import dotenv_values


async def main():
    key = dotenv_values(Path(__file__).parents[1] / ".env")["APP_SECRET_KEY"]
    headers = {"X-API-Key": key}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", headers=headers, timeout=60) as client:
        response = await client.get("/api/v1/settings/timeframe-profiles")
        response.raise_for_status()
        timeframe = response.json()["roles"]["trigger"]["timeframe"]
        params = {"symbol": "BTC/USDT", "timeframe": timeframe, "market_type": "crypto", "exchange": "binance"}
        chart = await client.get("/api/v1/chart/overlay", params=params)
        chart.raise_for_status()
        chart_data = chart.json()
        async with websockets.connect("ws://127.0.0.1:8000/ws/signals", additional_headers=headers) as ws:
            # Intentionally request a different TF. Server must use configured execution.
            await ws.send(json.dumps({"action": "subscribe", "symbol": "BTC/USDT", "timeframe": "4h"}))
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=65))
                if message.get("type") == "signal":
                    streamed = message["data"]
                    break
        expected = chart_data["analysis_snapshot"]
        actual = streamed["analysis_snapshot"]
        assert expected["snapshot_id"] == actual["snapshot_id"], (expected, actual)
        assert actual["timeframe"] == timeframe
        assert chart_data["strategy"]["approved"] == streamed["strategy"]["approved"]
        scanned = await client.get("/api/v1/signals/", params={"mode": "paper"})
        scanned.raise_for_status()
        scanner = next(s for s in scanned.json()["signals"] if s["symbol"] == "BTC/USDT")
        assert scanner["analysis_snapshot"]["snapshot_id"] == expected["snapshot_id"]
        assert scanner["strategy_approved"] == streamed["strategy"]["approved"]
        assert (await client.get("/api/v1/settings/timeframe-profiles", headers={"X-API-Key": "invalid"})).status_code == 401
        print(json.dumps({"timeframe": timeframe, "chart_ws_snapshot_match": True, "scanner_snapshot_match": True,
                          "strategy_match": True, "invalid_key_rejected": True}))


if __name__ == "__main__":
    asyncio.run(main())
