import pytest

from nexus_worker.documentation.hub import (
    DocumentationScopeError,
    attest_documentation_runtime,
    documentation_runtime_config,
    validate_documentation_path,
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
