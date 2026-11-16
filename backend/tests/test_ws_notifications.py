import json
import pytest

from app.api import ws as ws_module


class _DummyWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.mark.asyncio
async def test_broadcast_delivers_app_notification_to_stream_and_chat_clients():
    stream_ws = _DummyWebSocket()
    signals_ws = _DummyWebSocket()
    chat_ws = _DummyWebSocket()
    unregistered_ws = _DummyWebSocket()

    await ws_module._try_acquire_connection_slot(stream_ws, ws_module._stream_clients)
    await ws_module._try_acquire_connection_slot(signals_ws, ws_module._connections)
    await ws_module._try_acquire_connection_slot(chat_ws, ws_module._chat_clients)

    try:
        payload = {
            "type": "app_notification",
            "data": {"title": "Test Alert", "body": "SOL/USDT SHORT"},
        }
        await ws_module.broadcast(payload)
        await ws_module._send_json(unregistered_ws, payload)

        assert stream_ws.sent == [payload]
        assert signals_ws.sent == [payload]
        assert chat_ws.sent == [payload]
        assert unregistered_ws.sent == []
    finally:
        ws_module._remove_client(stream_ws)
        ws_module._remove_client(signals_ws)
        ws_module._remove_client(chat_ws)
