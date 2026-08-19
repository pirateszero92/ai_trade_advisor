"""API integration tests for FastAPI backend."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.anyio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"


@pytest.mark.anyio
async def test_get_active_prompt():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/settings/prompts/active")
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "content" in data
        assert "Apex" in data["content"] or "trading" in data["content"].lower()


@pytest.mark.anyio
async def test_prompt_history():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/settings/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompts" in data
        assert len(data["prompts"]) >= 1
