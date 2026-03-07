import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nexus_worker import agent
from nexus_worker.api import capabilities, health, infer, infer_stream, models
from nexus_worker.observability import install_observability


@pytest.fixture
def nx_worker_app():
    app = FastAPI()
    install_observability(app)
    app.include_router(health.router)
    app.include_router(capabilities.router)
    app.include_router(models.router)
    app.include_router(infer.router)
    app.include_router(infer_stream.router)
    app.state.worker_config = {
        "id": "nx1",
        "name": "Nexus Worker",
        "ollama_host": "http://localhost:11434",
        "capabilities": [],
    }
    return app


@pytest.mark.anyio
async def test_nexus_worker_health(nx_worker_app):
    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.anyio
async def test_nexus_worker_infer_ollama(nx_worker_app):
    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch(
            "nexus_worker.services.inference.ollama_backend.infer",
            new=AsyncMock(return_value={"output": "ok", "usage": {}}),
        ):
            resp = await client.post(
                "/infer",
                json={
                    "model": "llama3",
                    "provider": "ollama",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    assert resp.status_code == 200
    assert resp.json()["output"] == "ok"


@pytest.mark.anyio
async def test_ollama_backend_timeout_maps_to_504():
    from fastapi import HTTPException
    from nexus_worker.backends import ollama_backend

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            raise httpx.TimeoutException("timeout")

    with patch("nexus_worker.backends.ollama_backend.httpx.AsyncClient", return_value=FakeClient()):
        with pytest.raises(HTTPException) as exc:
            await ollama_backend.infer(
                model="llama3.1:8b",
                messages=[{"role": "user", "content": "hello"}],
                params={},
                host="http://localhost:11434",
            )

    assert exc.value.status_code == 504


@pytest.mark.anyio
async def test_ollama_backend_connect_error_maps_to_502():
    from fastapi import HTTPException
    from nexus_worker.backends import ollama_backend

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            raise httpx.ConnectError("refused")

    with patch("nexus_worker.backends.ollama_backend.httpx.AsyncClient", return_value=FakeClient()):
        with pytest.raises(HTTPException) as exc:
            await ollama_backend.infer(
                model="llama3.1:8b",
                messages=[{"role": "user", "content": "hello"}],
                params={},
                host="http://localhost:11434",
            )

    assert exc.value.status_code == 502


@pytest.mark.anyio
async def test_nexus_worker_cloud_context_policy_block(nx_worker_app, monkeypatch):
    monkeypatch.setenv("NEXUS_WORKER_CLOUD_CONTEXT_POLICY", "block")
    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        resp = await client.post(
            "/infer",
            json={
                "model": "gpt-4o-mini",
                "provider": "openai",
                "messages": [{"role": "system", "content": "Context:\nprivate"}],
            },
        )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_nexus_worker_metrics_endpoint(nx_worker_app):
    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        await client.get("/health")
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "nexus_worker_http_requests_total" in resp.text


@pytest.mark.anyio
async def test_nexus_worker_pull_local_model(nx_worker_app):
    class FakeResponse:
        status_code = 200
        text = '{"status":"success"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    with patch("nexus_worker.api.models.httpx.AsyncClient", return_value=FakeClient()):
        async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
            resp = await client.post("/models/local/pull", json={"model": "llama3.1:8b"})

    assert resp.status_code == 200
    assert resp.json()["model"] == "llama3.1:8b"


def test_nexus_worker_auto_register_defaults_off(monkeypatch):
    monkeypatch.delenv("NEXUS_WORKER_AUTO_REGISTER", raising=False)
    assert agent._auto_register_enabled() is False


def test_nexus_worker_auto_register_can_be_enabled(monkeypatch):
    monkeypatch.setenv("NEXUS_WORKER_AUTO_REGISTER", "1")
    assert agent._auto_register_enabled() is True


@pytest.mark.anyio
async def test_heartbeat_reregisters_after_404(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("boom", request=None, response=None)

    class FakeClient:
        def __init__(self, responses):
            self._responses = list(responses)
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            self.calls.append((url, json))
            return self._responses.pop(0)

    import httpx

    monkeypatch.setenv("CONTROL_PLANE_URL", "http://cp:8000")
    fake = FakeClient([FakeResponse(404), FakeResponse(200), FakeResponse(200)])
    app = FastAPI()
    app.state.worker_config = {"id": "nx1", "name": "Worker", "host": "localhost", "port": 8011}
    app.state.inference_inflight = 0

    sleeps = {"count": 0}

    async def _fake_sleep(_seconds):
        sleeps["count"] += 1
        if sleeps["count"] > 1:
            raise asyncio.CancelledError()

    with patch("nexus_worker.agent.detect_hardware_profile", return_value={"cpu": {}, "gpus": []}), \
         patch("nexus_worker.agent.httpx.AsyncClient", return_value=fake), \
         patch("nexus_worker.agent.asyncio.sleep", new=_fake_sleep):
        task = asyncio.create_task(agent._send_heartbeats("nx1", app))
        with pytest.raises(asyncio.CancelledError):
            await task

    assert fake.calls[0][0].endswith("/v1/workers/nx1/heartbeat")
    assert fake.calls[1][0].endswith("/v1/workers")
    assert fake.calls[2][0].endswith("/v1/workers/nx1/heartbeat")
