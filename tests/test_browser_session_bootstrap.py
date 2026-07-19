import pytest

from nexus_worker.browser.session_bootstrap import (
    BrowserSessionBootstrapError,
    browser_session_bootstrap_settings,
)


def _worker_config() -> dict:
    return {
        "tooling": {
            "browser": {
                "enabled": True,
                "base_url": "https://app.example",
                "allowed_paths": ["/admin/login", "/admin/courses"],
                "user_data_dir": "/private/profile",
                "headless": True,
                "session_check": {
                    "required": True,
                    "path": "/admin/courses",
                    "authenticated_selector": "h2",
                },
                "session_bootstrap": {
                    "enabled": True,
                    "login_path": "/admin/login",
                    "username_env": "NEXUS_TEST_USERNAME",
                    "password_env": "NEXUS_TEST_PASSWORD",
                    "username_selector": "#username",
                    "password_selector": "#password",
                    "submit_selector": "button[type=submit]",
                },
            }
        }
    }


def test_browser_session_bootstrap_reads_only_declared_private_credentials(monkeypatch):
    monkeypatch.setenv("NEXUS_TEST_USERNAME", "operator@example.test")
    monkeypatch.setenv("NEXUS_TEST_PASSWORD", "test-password")

    settings = browser_session_bootstrap_settings(_worker_config())

    assert settings.login_url == "https://app.example/admin/login"
    assert settings.profile_dir == "/private/profile"
    assert settings.username == "operator@example.test"
    assert settings.password == "test-password"


def test_browser_session_bootstrap_allows_bounded_multi_minute_login(monkeypatch):
    monkeypatch.setenv("NEXUS_TEST_USERNAME", "operator@example.test")
    monkeypatch.setenv("NEXUS_TEST_PASSWORD", "test-password")
    worker_config = _worker_config()
    worker_config["tooling"]["browser"]["session_bootstrap"]["timeout_seconds"] = 300

    settings = browser_session_bootstrap_settings(worker_config)

    assert settings.timeout_ms == 300_000


def test_browser_session_bootstrap_rejects_missing_private_credentials(monkeypatch):
    monkeypatch.delenv("NEXUS_TEST_USERNAME", raising=False)
    monkeypatch.delenv("NEXUS_TEST_PASSWORD", raising=False)

    with pytest.raises(BrowserSessionBootstrapError, match="credentials are missing"):
        browser_session_bootstrap_settings(_worker_config())


def test_browser_session_bootstrap_rejects_login_path_outside_scope(monkeypatch):
    monkeypatch.setenv("NEXUS_TEST_USERNAME", "operator@example.test")
    monkeypatch.setenv("NEXUS_TEST_PASSWORD", "test-password")
    worker_config = _worker_config()
    worker_config["tooling"]["browser"]["session_bootstrap"]["login_path"] = "/admin/users"

    with pytest.raises(BrowserSessionBootstrapError, match="outside the declared scope"):
        browser_session_bootstrap_settings(worker_config)
