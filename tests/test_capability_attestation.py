from nexus_worker.capability_attestation import attest_worker_capabilities


def test_attestation_registers_only_explicitly_enabled_installed_cli_tools():
    effective, report = attest_worker_capabilities(
        {
            "capabilities": [
                {"type": "llm", "provider": "ollama_cloud", "models": ["glm-5.2:cloud"]},
                {"type": "tool", "provider": "cli", "models": ["codex", "claude"]},
                {"type": "tool", "provider": "browser", "models": ["browser-ui"]},
            ],
            "tooling": {"cli_tools": ["codex", "missing-cli", "CODEX"]},
        },
        [
            {"name": "codex", "path": "/usr/local/bin/codex"},
            {"name": "git", "path": "/usr/bin/git"},
        ],
    )

    assert effective["capabilities"] == [
        {"type": "llm", "provider": "ollama_cloud", "models": ["glm-5.2:cloud"]},
        {"type": "tool", "provider": "cli", "models": ["codex"]},
    ]
    assert effective["tooling"]["cli_tools"] == ["codex"]
    assert report == {
        "configured_cli_tools": ["codex", "missing-cli"],
        "installed_cli_tools": ["codex", "git"],
        "enabled_cli_tools": ["codex"],
        "unavailable_cli_tools": ["missing-cli"],
        "auth_required_cli_tools": [],
        "unauthenticated_cli_tools": [],
        "provider_credentials": {"ollama_cloud": False},
        "discarded_declared_tool_capabilities": 2,
        "browser": {"configured": False, "ready": False},
        "documentation": {"configured": False, "ready": False},
    }


def test_attestation_reports_cloud_credential_presence_without_exposing_it(monkeypatch):
    config = {
        "capabilities": [
            {"type": "llm", "provider": "ollama_cloud", "models": ["glm-5.2:cloud"]},
            {"type": "llm", "provider": "ollama", "models": ["llama3.2"]},
        ]
    }

    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    _, missing = attest_worker_capabilities(config, [])

    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    _, configured = attest_worker_capabilities(config, [])

    assert missing["provider_credentials"] == {"ollama_cloud": False}
    assert configured["provider_credentials"] == {"ollama_cloud": True}


def test_attestation_does_not_enable_discovered_tools_without_policy():
    effective, report = attest_worker_capabilities(
        {"capabilities": [{"type": "tool", "provider": "cli", "models": ["codex"]}]},
        [{"name": "codex", "path": "/usr/local/bin/codex"}],
    )

    assert effective["capabilities"] == []
    assert effective["tooling"]["cli_tools"] == []
    assert report["enabled_cli_tools"] == []


def test_attestation_blocks_cli_tools_that_require_authentication_until_ready():
    effective, report = attest_worker_capabilities(
        {
            "capabilities": [{"type": "tool", "provider": "cli", "models": ["codex"]}],
            "tooling": {"cli_tools": ["codex"]},
        },
        [
            {
                "name": "codex",
                "path": "/usr/local/bin/codex",
                "requires_authentication": True,
                "authentication_state": "not_authenticated",
            }
        ],
    )

    assert effective["capabilities"] == []
    assert effective["tooling"]["cli_tools"] == []
    assert report["auth_required_cli_tools"] == ["codex"]
    assert report["unauthenticated_cli_tools"] == ["codex"]


def test_attestation_adds_browser_only_after_runtime_proof():
    config = {
        "capabilities": [{"type": "tool", "provider": "browser", "models": ["browser-ui"]}],
        "tooling": {"browser": {"enabled": True}},
    }

    unavailable, unavailable_report = attest_worker_capabilities(
        config,
        [],
        {"configured": True, "ready": False, "reason": "chromium_not_installed"},
    )
    ready, ready_report = attest_worker_capabilities(
        config,
        [],
        {"configured": True, "ready": True, "browser": "chromium"},
    )

    assert unavailable["capabilities"] == []
    assert unavailable_report["browser"]["ready"] is False
    assert ready["capabilities"] == [
        {"type": "tool", "provider": "browser", "models": ["browser-ui"]}
    ]
    assert ready_report["browser"] == {"configured": True, "ready": True, "browser": "chromium"}


def test_attestation_adds_documentation_only_when_configured_credentials_are_present(monkeypatch):
    config = {
        "capabilities": [{"type": "tool", "provider": "documentation", "models": ["documentation-v1"]}],
        "tooling": {"documentation_hub": {"enabled": True}},
    }

    unavailable, unavailable_report = attest_worker_capabilities(
        config,
        [],
        documentation_attestation={"configured": True, "ready": False, "reason": "credentials_unavailable"},
    )
    ready, ready_report = attest_worker_capabilities(
        config,
        [],
        documentation_attestation={"configured": True, "ready": True, "provider": "documentation"},
    )

    assert unavailable["capabilities"] == []
    assert unavailable_report["documentation"]["ready"] is False
    assert ready["capabilities"] == [
        {"type": "tool", "provider": "documentation", "models": ["documentation-v1"]}
    ]
    assert ready_report["documentation"]["ready"] is True
