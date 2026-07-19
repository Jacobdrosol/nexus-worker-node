import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nexus_worker import agent
from nexus_worker.api import browser, capabilities, health, infer, infer_stream, models
from nexus_worker.browser.inspector import BrowserScopeError
from nexus_worker.browser.question_bank import validate_question_bank_patch
from nexus_worker.browser.test_builder import validate_test_builder_action
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
    app.include_router(browser.router)
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
    assert resp.json()["enabled_cli_tools"] == []
    assert resp.json()["browser_ready"] is False


@pytest.mark.anyio
async def test_worker_startup_attests_capabilities_before_registration(monkeypatch):
    app = FastAPI()
    monkeypatch.setenv("NEXUS_WORKER_AUTO_REGISTER", "0")
    declared = {
        "id": "nx-attested",
        "name": "Attested Worker",
        "capabilities": [
            {"type": "llm", "provider": "ollama_cloud", "models": ["glm-5.2:cloud"]},
            {"type": "tool", "provider": "cli", "models": ["codex"]},
        ],
        "tooling": {"cli_tools": ["codex", "claude"]},
    }
    with patch("nexus_worker.agent.ConfigLoader.load_yaml", return_value=declared), patch(
        "nexus_worker.agent.discover_cli_tools",
        return_value=[{"name": "codex", "path": "/usr/local/bin/codex"}],
    ):
        async with agent.lifespan(app):
            assert app.state.worker_config["capabilities"] == [
                {"type": "llm", "provider": "ollama_cloud", "models": ["glm-5.2:cloud"]},
                {"type": "tool", "provider": "cli", "models": ["codex"]},
            ]
            assert app.state.worker_config["tooling"]["cli_tools"] == ["codex"]
            assert app.state.capability_attestation["unavailable_cli_tools"] == ["claude"]
            assert app.state.browser_attestation == {"configured": False, "ready": False}


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
async def test_nexus_worker_rejects_cli_without_declared_tooling(nx_worker_app):
    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch("nexus_worker.services.inference.cli_backend.infer", new=AsyncMock()) as mock_infer:
            resp = await client.post(
                "/infer",
                json={
                    "model": "claude",
                    "provider": "cli",
                    "messages": [],
                },
            )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "No CLI tools are enabled on this worker"
    mock_infer.assert_not_awaited()


@pytest.mark.anyio
async def test_nexus_worker_rejects_browser_without_attested_runtime(nx_worker_app):
    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        resp = await client.post("/browser/inspect", json={"path": "/admin/courses"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Read-only browser tooling is not available on this worker"


@pytest.mark.anyio
async def test_nexus_worker_rejects_browser_inspection_without_request_token(nx_worker_app, monkeypatch):
    nx_worker_app.state.browser_runtime_config = {
        "enabled": True,
        "base_url": "https://example.test",
        "allowed_paths": ["/admin/*"],
        "user_data_dir": "/private/profile",
        "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
    }
    nx_worker_app.state.browser_attestation = {"configured": True, "ready": True, "browser": "chromium"}
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "node-secret")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        resp = await client.post("/browser/inspect", json={"path": "/admin/courses"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Browser worker request token is invalid"


@pytest.mark.anyio
async def test_nexus_worker_runs_read_only_browser_inspection(nx_worker_app, monkeypatch):
    nx_worker_app.state.browser_runtime_config = {
        "enabled": True,
        "base_url": "https://example.test",
        "allowed_paths": ["/admin/*"],
        "user_data_dir": "/private/profile",
        "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
    }
    nx_worker_app.state.browser_attestation = {"configured": True, "ready": True, "browser": "chromium"}
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "node-secret")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch(
            "nexus_worker.api.browser.inspect_page",
            new=AsyncMock(return_value={"url": "https://example.test/admin/courses", "text": "Courses"}),
        ) as mock_inspect:
            resp = await client.post(
                "/browser/inspect",
                json={"path": "/admin/courses", "text_limit": 500},
                headers={"X-Nexus-Worker-Token": "node-secret"},
            )

    assert resp.status_code == 200
    assert resp.json()["text"] == "Courses"
    mock_inspect.assert_awaited_once_with(
        nx_worker_app.state.browser_runtime_config,
        path="/admin/courses",
        text_limit=500,
        element_limit=40,
    )


@pytest.mark.anyio
async def test_nexus_worker_rejects_unenabled_test_builder_actions(nx_worker_app, monkeypatch):
    nx_worker_app.state.browser_runtime_config = {
        "enabled": True,
        "base_url": "https://example.test",
        "allowed_paths": ["/admin/*"],
        "user_data_dir": "/private/profile",
        "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
    }
    nx_worker_app.state.browser_attestation = {"configured": True, "ready": True, "browser": "chromium"}
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "node-secret")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        resp = await client.post(
            "/browser/test-builder",
            json={
                "action": "save_configuration",
                "confirmation": "approved:test-builder:save_configuration",
                "course_id": 60,
                "lesson_id": 601,
                "pass_threshold_pct": 70,
                "allow_review": False,
                "banks": [{"name": "Lesson 1", "easy": 1}],
            },
            headers={"X-Nexus-Worker-Token": "node-secret"},
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Test Builder actions are not enabled on this worker"


@pytest.mark.anyio
async def test_nexus_worker_rejects_arbitrary_test_builder_fields(nx_worker_app, monkeypatch):
    nx_worker_app.state.browser_runtime_config = {
        "enabled": True,
        "base_url": "https://example.test",
        "allowed_paths": ["/admin/*"],
        "user_data_dir": "/private/profile",
        "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
    }
    nx_worker_app.state.browser_attestation = {"configured": True, "ready": True, "browser": "chromium"}
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "node-secret")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        resp = await client.post(
            "/browser/test-builder",
            json={
                "action": "save_configuration",
                "confirmation": "approved:test-builder:save_configuration",
                "course_id": 60,
                "lesson_id": 601,
                "pass_threshold_pct": 70,
                "allow_review": False,
                "banks": [{"name": "Lesson 1", "easy": 1}],
                "selector": "button.publish",
            },
            headers={"X-Nexus-Worker-Token": "node-secret"},
        )

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_nexus_worker_dispatches_only_confirmed_test_builder_actions(nx_worker_app, monkeypatch):
    nx_worker_app.state.browser_runtime_config = {
        "enabled": True,
        "base_url": "https://example.test",
        "allowed_paths": ["/admin/*"],
        "user_data_dir": "/private/profile",
        "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
        "assessment_test_builder": {
            "enabled": True,
            "allowed_actions": ["save_configuration"],
        },
    }
    nx_worker_app.state.browser_attestation = {"configured": True, "ready": True, "browser": "chromium"}
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "node-secret")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch(
            "nexus_worker.api.browser.execute_test_builder_action",
            new=AsyncMock(return_value={"status": "Test configuration saved successfully"}),
        ) as mock_action:
            resp = await client.post(
                "/browser/test-builder",
                json={
                    "action": "save_configuration",
                    "confirmation": "approved:test-builder:save_configuration",
                    "course_id": 60,
                    "lesson_id": 601,
                    "title": "Lesson 1 Quiz",
                    "pass_threshold_pct": 70,
                    "time_limit_seconds": 900,
                    "allow_review": True,
                    "banks": [{"name": "Lesson 1", "easy": 1, "medium": 2}],
                },
                headers={"X-Nexus-Worker-Token": "node-secret"},
            )

    assert resp.status_code == 200
    assert resp.json()["status"] == "Test configuration saved successfully"
    assert mock_action.await_args.kwargs["action"] == "save_configuration"
    assert mock_action.await_args.kwargs["banks"] == [
        {"name": "Lesson 1", "easy": 1, "medium": 2, "hard": 0, "apply": 0}
    ]


def test_test_builder_validation_rejects_publish_and_unacknowledged_attempt_resets():
    browser_config = {
        "assessment_test_builder": {
            "enabled": True,
            "allowed_actions": ["save_configuration", "build_from_banks"],
        }
    }
    request = {
        "mode": "draft",
        "confirmation": "approved:test-builder:build_from_banks",
        "course_id": 60,
        "lesson_id": 601,
        "banks": [{"name": "Lesson 1", "easy": 1}],
    }

    with pytest.raises(BrowserScopeError, match="Publishing is prohibited"):
        validate_test_builder_action(browser_config, action="publish", acknowledge_attempt_reset=True, **request)
    with pytest.raises(BrowserScopeError, match="attempt-reset acknowledgement"):
        validate_test_builder_action(browser_config, action="build_from_banks", acknowledge_attempt_reset=False, **request)


@pytest.mark.anyio
async def test_nexus_worker_rejects_unenabled_question_bank_patch(nx_worker_app, monkeypatch):
    nx_worker_app.state.browser_runtime_config = {
        "enabled": True,
        "base_url": "https://example.test",
        "allowed_paths": ["/admin/*"],
        "user_data_dir": "/private/profile",
        "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
    }
    nx_worker_app.state.browser_attestation = {"configured": True, "ready": True, "browser": "chromium"}
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "node-secret")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        resp = await client.post(
            "/browser/question-bank",
            json={
                "action": "patch_existing",
                "confirmation": "approved:question-bank:patch_existing:42:7",
                "bank_id": 42,
                "question_id": 7,
                "expected": {"prompt": "What is 2 + 2?", "question_type": "MCQ"},
                "changes": {"prompt": "What is 3 + 1?"},
                "review_evidence": {
                    "reviewer_bot_id": "globeiq-question-bank-review-01-bot",
                    "review_task_id": "review-42-7",
                    "approved_patch": True,
                    "semantic_duplicate_risk": "materially_distinct_context",
                    "reviewed_question_ids": [7],
                    "shortage_detected": False,
                    "rationale": "The reviewer checked the target and comparable questions for duplication.",
                },
            },
            headers={"X-Nexus-Worker-Token": "node-secret"},
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Question Bank patches are not enabled on this worker"


@pytest.mark.anyio
async def test_nexus_worker_dispatches_only_confirmed_question_bank_patch(nx_worker_app, monkeypatch):
    nx_worker_app.state.browser_runtime_config = {
        "enabled": True,
        "base_url": "https://example.test",
        "allowed_paths": ["/admin/*"],
        "user_data_dir": "/private/profile",
        "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
        "question_bank_patch": {
            "enabled": True,
            "allowed_actions": ["patch_existing"],
            "reviewer_bot_id": "globeiq-question-bank-review-01-bot",
        },
    }
    nx_worker_app.state.browser_attestation = {"configured": True, "ready": True, "browser": "chromium"}
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "node-secret")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch(
            "nexus_worker.api.browser.execute_question_bank_patch",
            new=AsyncMock(return_value={"status": "Question Bank patch saved and verified"}),
        ) as mock_action:
            resp = await client.post(
                "/browser/question-bank",
                json={
                    "action": "patch_existing",
                    "confirmation": "approved:question-bank:patch_existing:42:7",
                    "bank_id": 42,
                    "question_id": 7,
                    "expected": {
                        "prompt": "What is 2 + 2?",
                        "question_type": "MCQ",
                        "difficulty": "easy",
                        "options": ["2", "3", "4"],
                        "correct_option_index": 2,
                    },
                    "changes": {"prompt": "What is 3 + 1?"},
                    "review_evidence": {
                        "reviewer_bot_id": "globeiq-question-bank-review-01-bot",
                        "review_task_id": "review-42-7",
                        "approved_patch": True,
                        "semantic_duplicate_risk": "materially_distinct_context",
                        "reviewed_question_ids": [7],
                        "shortage_detected": False,
                        "rationale": "The reviewer checked the target and comparable questions for duplication.",
                    },
                },
                headers={"X-Nexus-Worker-Token": "node-secret"},
            )

    assert resp.status_code == 200
    assert resp.json()["status"] == "Question Bank patch saved and verified"
    assert mock_action.await_args.kwargs["question_id"] == 7
    assert mock_action.await_args.kwargs["changes"] == {"prompt": "What is 3 + 1?"}


def test_question_bank_patch_validation_rejects_cross_question_confirmation_and_option_count_changes():
    browser_config = {
        "question_bank_patch": {
            "enabled": True,
            "allowed_actions": ["patch_existing"],
            "reviewer_bot_id": "globeiq-question-bank-review-01-bot",
        }
    }
    request = {
        "action": "patch_existing",
        "confirmation": "approved:question-bank:patch_existing:42:7",
        "bank_id": 42,
        "question_id": 7,
        "expected": {
            "prompt": "What is 2 + 2?",
            "question_type": "MCQ",
            "options": ["2", "3", "4"],
            "correct_option_index": 2,
        },
        "review_evidence": {
            "reviewer_bot_id": "globeiq-question-bank-review-01-bot",
            "review_task_id": "review-42-7",
            "approved_patch": True,
            "semantic_duplicate_risk": "materially_distinct_context",
            "reviewed_question_ids": [7],
            "shortage_detected": False,
            "rationale": "The reviewer checked the target and comparable questions for duplication.",
        },
    }

    with pytest.raises(BrowserScopeError, match="exact single-question confirmation"):
        validate_question_bank_patch(
            browser_config,
            changes={"prompt": "What is 3 + 1?"},
            **{**request, "question_id": 8},
        )
    with pytest.raises(BrowserScopeError, match="unauthorized reviewer"):
        validate_question_bank_patch(
            browser_config,
            changes={"prompt": "What is 3 + 1?"},
            **{
                **request,
                "review_evidence": {
                    **request["review_evidence"],
                    "reviewer_bot_id": "untrusted-reviewer",
                },
            },
        )
    with pytest.raises(BrowserScopeError, match="cannot add or remove options"):
        validate_question_bank_patch(
            browser_config,
            changes={"options": ["1", "2", "3", "4"]},
            **request,
        )


@pytest.mark.anyio
async def test_nexus_worker_runs_only_declared_cli_tool(nx_worker_app):
    nx_worker_app.state.worker_config["tooling"] = {"cli_tools": ["claude"]}

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch(
            "nexus_worker.services.inference.cli_backend.infer",
            new=AsyncMock(return_value={"output": "ok", "usage": {}}),
        ) as mock_infer:
            resp = await client.post(
                "/infer",
                json={
                    "model": "claude",
                    "provider": "cli",
                    "command": "claude -p 'review this change'",
                    "messages": [{"role": "user", "content": "Review the worker change."}],
                },
            )

    assert resp.status_code == 200
    assert resp.json()["output"] == "ok"
    mock_infer.assert_awaited_once_with(
        command="claude -p 'review this change'",
        params={},
        allowed_tools={"claude"},
        input_text="user:\nReview the worker change.",
    )


@pytest.mark.anyio
async def test_nexus_worker_infer_ollama_cloud(nx_worker_app, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch(
            "nexus_worker.services.inference.ollama_cloud_backend.infer",
            new=AsyncMock(return_value={"output": "cloud-ok", "usage": {}}),
        ) as mock_infer:
            resp = await client.post(
                "/infer",
                json={
                    "model": "glm-5.2:cloud",
                    "provider": "ollama_cloud",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

    assert resp.status_code == 200
    assert resp.json()["output"] == "cloud-ok"
    mock_infer.assert_awaited_once()


@pytest.mark.anyio
async def test_nexus_worker_ollama_cloud_redacts_context(nx_worker_app, monkeypatch):
    monkeypatch.setenv("NEXUS_WORKER_CLOUD_CONTEXT_POLICY", "redact")
    captured = {}

    async def _fake_infer(**kwargs):
        captured.update(kwargs)
        return {"output": "ok", "usage": {}}

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch("nexus_worker.services.inference.ollama_cloud_backend.infer", new=_fake_infer):
            resp = await client.post(
                "/infer",
                json={
                    "model": "glm-5.2:cloud",
                    "provider": "ollama_cloud",
                    "messages": [{"role": "system", "content": "Context:\nprivate"}],
                },
            )

    assert resp.status_code == 200
    assert resp.json()["policy_context_redacted"] is True
    assert captured["messages"][0]["content"] == "Context:\n[REDACTED_BY_POLICY]"


@pytest.mark.anyio
async def test_ollama_cloud_backend_requires_api_key(monkeypatch):
    from fastapi import HTTPException
    from nexus_worker.backends import ollama_cloud_backend

    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        await ollama_cloud_backend.infer(
            model="glm-5.2:cloud",
            messages=[{"role": "user", "content": "hello"}],
            params={},
        )

    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_ollama_cloud_backend_maps_max_tokens_to_num_predict(monkeypatch):
    from nexus_worker.backends import ollama_cloud_backend

    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "ok"}, "prompt_eval_count": 1, "eval_count": 2}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    with patch("nexus_worker.backends.ollama_cloud_backend.httpx.AsyncClient", return_value=FakeClient()):
        result = await ollama_cloud_backend.infer(
            model="glm-5.2:cloud",
            messages=[{"role": "user", "content": "hello"}],
            params={"max_tokens": 128},
        )

    assert result["output"] == "ok"
    assert captured["url"] == "https://ollama.com/api/chat"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["think"] is False
    assert captured["json"]["options"]["num_predict"] == 128
    assert "max_tokens" not in captured["json"]["options"]


@pytest.mark.anyio
async def test_ollama_cloud_backend_removes_reasoning_markup(monkeypatch):
    from nexus_worker.backends import ollama_cloud_backend

    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {"content": "<think>internal analysis</think>Draft patch</think>"},
                "prompt_eval_count": 1,
                "eval_count": 2,
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            return FakeResponse()

    with patch("nexus_worker.backends.ollama_cloud_backend.httpx.AsyncClient", return_value=FakeClient()):
        result = await ollama_cloud_backend.infer(
            model="glm-5.2:cloud",
            messages=[{"role": "user", "content": "hello"}],
            params={},
        )

    assert result["output"] == "Draft patch"


def test_ollama_cloud_stream_filter_suppresses_split_reasoning_tags():
    from nexus_worker.backends.ollama_cloud_backend import _VisibleOutputFilter

    output = _VisibleOutputFilter()
    parts = [
        output.feed("<thi"),
        output.feed("nk>internal"),
        output.feed(" analysis</thi"),
        output.feed("nk>Draft patch"),
        output.finish(),
    ]

    assert "".join(parts) == "Draft patch"


def test_ollama_cloud_chat_body_allows_explicit_thinking():
    from nexus_worker.backends.ollama_cloud_backend import _chat_body

    body = _chat_body(
        model="glm-5.2:cloud",
        messages=[{"role": "user", "content": "hello"}],
        params={"think": "low", "max_tokens": 64},
        stream=False,
    )

    assert body["think"] == "low"
    assert body["options"]["num_predict"] == 64
    assert "think" not in body["options"]


@pytest.mark.anyio
async def test_ollama_backend_maps_max_tokens_to_num_predict():
    from nexus_worker.backends import ollama_backend

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "ok"}, "prompt_eval_count": 1, "eval_count": 1}

    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    with patch("nexus_worker.backends.ollama_backend.httpx.AsyncClient", return_value=FakeClient()):
        result = await ollama_backend.infer(
            model="llama3.1:8b",
            messages=[{"role": "user", "content": "hello"}],
            params={"max_tokens": 512, "temperature": 0.2},
            host="http://localhost:11434",
        )

    assert result["output"] == "ok"
    assert captured["json"]["options"]["num_predict"] == 512
    assert "max_tokens" not in captured["json"]["options"]
    assert captured["json"]["options"]["temperature"] == 0.2


def test_ollama_backend_timeout_disables_read_deadline():
    from nexus_worker.backends import ollama_backend

    timeout = ollama_backend._ollama_timeout()
    assert timeout.connect == 10.0
    assert timeout.read is None
    assert timeout.write == 120.0


@pytest.mark.anyio
async def test_nexus_worker_infer_stream_ollama(nx_worker_app):
    async def _fake_stream(**kwargs):
        yield {"event": "token", "text": "hel"}
        yield {"event": "token", "text": "lo"}
        yield {"event": "final", "output": "hello", "usage": {"prompt_tokens": 1, "completion_tokens": 2}}

    async with AsyncClient(transport=ASGITransport(app=nx_worker_app), base_url="http://test") as client:
        with patch(
            "nexus_worker.api.infer_stream.run_inference_stream",
            new=_fake_stream,
        ):
            resp = await client.post(
                "/infer/stream",
                json={
                    "model": "llama3",
                    "provider": "ollama",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    assert resp.status_code == 200
    assert "event: token" in resp.text
    assert '"text": "hel"' in resp.text
    assert "event: final" in resp.text


@pytest.mark.anyio
async def test_ollama_backend_stream_finalizes_immediately_on_done():
    from nexus_worker.backends import ollama_backend

    chunks = [
        json.dumps({"message": {"content": "hel"}, "done": False}),
        json.dumps({
            "message": {"content": "lo"},
            "done": True,
            "prompt_eval_count": 1,
            "eval_count": 2,
        }),
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            for chunk in chunks:
                yield chunk
            while True:
                await asyncio.sleep(10)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json=None):
            return FakeResponse()

    events = []
    with patch("nexus_worker.backends.ollama_backend.httpx.AsyncClient", return_value=FakeClient()):
        async for event in ollama_backend.infer_stream(
            model="llama3.1:8b",
            messages=[{"role": "user", "content": "hello"}],
            params={},
            host="http://localhost:11434",
        ):
            events.append(event)

    assert events == [
        {"event": "token", "text": "hel"},
        {"event": "token", "text": "lo"},
        {"event": "final", "output": "hello", "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
    ]


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


def test_registration_config_removes_private_browser_runtime_settings():
    registered = agent._registration_config(
        {
            "id": "browser-worker",
            "tooling": {
                "cli_tools": [],
                "browser": {
                    "enabled": True,
                    "base_url": "https://private.example",
                    "user_data_dir": "/private/profile",
                },
            },
        }
    )

    assert registered["tooling"] == {"cli_tools": []}


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
