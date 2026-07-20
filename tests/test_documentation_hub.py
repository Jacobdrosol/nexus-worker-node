import pytest
import httpx

from nexus_worker.documentation import hub
from nexus_worker.documentation.hub import (
    DocumentationScopeError,
    attest_documentation_runtime,
    documentation_runtime_config,
    validate_documentation_path,
    write_documentation,
)


def _config():
    return {
        "tooling": {
            "documentation_hub": {
                "enabled": True,
                "base_url": "https://globeiq.example",
                "username_env": "GLOBEIQ_ADMIN_EMAIL",
                "password_env": "GLOBEIQ_ADMIN_PASSWORD",
                "allowed_roots": ["docs/Automation_Workforce", "docs/Course_Generator"],
            }
        }
    }


def test_documentation_runtime_attests_only_when_credentials_are_present(monkeypatch):
    monkeypatch.delenv("GLOBEIQ_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("GLOBEIQ_ADMIN_PASSWORD", raising=False)
    assert attest_documentation_runtime(_config())["ready"] is False

    monkeypatch.setenv("GLOBEIQ_ADMIN_EMAIL", "worker@globaliq.local")
    monkeypatch.setenv("GLOBEIQ_ADMIN_PASSWORD", "private-test-secret")
    report = attest_documentation_runtime(_config())

    assert report["ready"] is True
    assert report["allowed_roots"] == ["docs/Automation_Workforce", "docs/Course_Generator"]
    assert "private-test-secret" not in str(report)


def test_documentation_path_rejects_traversal_extensions_and_unapproved_roots():
    config = documentation_runtime_config(_config())
    assert config is not None
    assert validate_documentation_path(config, "docs/Automation_Workforce/Content_Casey/report.md") == (
        "docs/Automation_Workforce/Content_Casey/report.md"
    )

    for path in ("../secrets.md", "docs/Automation_Workforce/../private.md", "docs/Automation_Workforce/report.json", "docs/Elsewhere/report.md"):
        with pytest.raises(DocumentationScopeError):
            validate_documentation_path(config, path)


@pytest.mark.asyncio
async def test_documentation_write_reuses_the_authenticated_session(monkeypatch):
    class FakeAsyncClient:
        calls: list[tuple[str, str]] = []

        def __init__(self, *, base_url, cookies=None, **_kwargs):
            self.base_url = base_url
            self.cookies = httpx.Cookies(cookies)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, json):
            self.calls.append((path, json.get("path", "")))
            request = httpx.Request("POST", f"{self.base_url}{path}")
            if path == "/api/admin/auth/login":
                self.cookies.set("docs-session", "cached")
                return httpx.Response(200, request=request)
            return httpx.Response(200, request=request)

    monkeypatch.setenv("GLOBEIQ_ADMIN_EMAIL", "worker@globeiq.local")
    monkeypatch.setenv("GLOBEIQ_ADMIN_PASSWORD", "private-test-secret")
    monkeypatch.setattr(hub.httpx, "AsyncClient", FakeAsyncClient)
    hub._SESSION_COOKIES.clear()

    await write_documentation(
        _config(),
        action="create",
        path="docs/Automation_Workforce/first.md",
        content="# First",
    )
    await write_documentation(
        _config(),
        action="create",
        path="docs/Automation_Workforce/second.md",
        content="# Second",
    )

    assert [path for path, _ in FakeAsyncClient.calls].count("/api/admin/auth/login") == 1
    assert [path for path, _ in FakeAsyncClient.calls].count("/api/admin/documentation/create") == 2
