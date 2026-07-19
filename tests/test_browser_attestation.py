from nexus_worker.browser.attestation import _authenticated_session_check, attest_browser_runtime


def test_browser_attestation_skips_disabled_browser_config():
    assert attest_browser_runtime({"tooling": {"browser": {"enabled": False}}}) == {
        "configured": False,
        "ready": False,
    }


def test_browser_attestation_rejects_invalid_scope_before_runtime_probe():
    report = attest_browser_runtime(
        {
            "tooling": {
                "browser": {
                    "enabled": True,
                    "base_url": "https://app.example",
                    "allowed_paths": ["/*"],
                    "user_data_dir": "/private/profile",
                }
            }
        }
    )

    assert report == {
        "configured": True,
        "ready": False,
        "reason": "browser_configuration_invalid",
    }


def test_browser_attestation_rejects_incomplete_required_session_check(monkeypatch):
    monkeypatch.setenv("NEXUS_BROWSER_WORKER_TOKEN", "worker-token")
    report = attest_browser_runtime(
        {
            "tooling": {
                "browser": {
                    "enabled": True,
                    "base_url": "https://app.example",
                    "allowed_paths": ["/admin/*"],
                    "user_data_dir": "/private/profile",
                    "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
                    "session_check": {"required": True, "path": "/admin/dashboard"},
                }
            }
        }
    )

    assert report == {
        "configured": True,
        "ready": False,
        "reason": "browser_configuration_invalid",
    }


def test_browser_attestation_requires_a_configured_request_token(monkeypatch):
    monkeypatch.delenv("NEXUS_BROWSER_WORKER_TOKEN", raising=False)
    report = attest_browser_runtime(
        {
            "tooling": {
                "browser": {
                    "enabled": True,
                    "base_url": "https://app.example",
                    "allowed_paths": ["/admin/*"],
                    "user_data_dir": "/private/profile",
                    "request_token_env": "NEXUS_BROWSER_WORKER_TOKEN",
                }
            }
        }
    )

    assert report == {
        "configured": True,
        "ready": False,
        "reason": "browser_request_token_missing",
    }


def test_browser_attestation_allows_bounded_multi_minute_session_checks():
    session_check = _authenticated_session_check(
        {
            "base_url": "https://app.example",
            "allowed_paths": ["/admin/*"],
            "session_check": {
                "required": True,
                "path": "/admin/dashboard",
                "authenticated_selector": "h2",
                "timeout_seconds": 300,
            },
        }
    )

    assert session_check is not None
    assert session_check["timeout_ms"] == 300_000
