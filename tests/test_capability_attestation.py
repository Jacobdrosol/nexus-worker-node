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
        "discarded_declared_tool_capabilities": 2,
        "browser": {"configured": False, "ready": False},
    }


def test_attestation_does_not_enable_discovered_tools_without_policy():
    effective, report = attest_worker_capabilities(
        {"capabilities": [{"type": "tool", "provider": "cli", "models": ["codex"]}]},
        [{"name": "codex", "path": "/usr/local/bin/codex"}],
    )

    assert effective["capabilities"] == []
    assert effective["tooling"]["cli_tools"] == []
    assert report["enabled_cli_tools"] == []


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
