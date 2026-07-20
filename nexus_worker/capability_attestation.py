"""Build the capability record a worker can truthfully register with NexusAI."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Iterable


_PROVIDER_CREDENTIAL_ENV = {
    "ollama_cloud": "OLLAMA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _tool_name(value: Any) -> str:
    return str(value or "").strip().casefold()


def _configured_cli_tools(worker_config: dict[str, Any]) -> set[str]:
    tooling = worker_config.get("tooling")
    if not isinstance(tooling, dict):
        return set()
    raw_tools = tooling.get("cli_tools")
    if not isinstance(raw_tools, list):
        return set()
    return {_tool_name(tool) for tool in raw_tools if _tool_name(tool)}


def _installed_cli_tools(discovered_tools: Iterable[dict[str, Any]]) -> set[str]:
    return {
        _tool_name(tool.get("name"))
        for tool in discovered_tools
        if isinstance(tool, dict) and _tool_name(tool.get("name"))
    }


def _auth_required_cli_tools(discovered_tools: Iterable[dict[str, Any]]) -> set[str]:
    return {
        _tool_name(tool.get("name"))
        for tool in discovered_tools
        if isinstance(tool, dict)
        and _tool_name(tool.get("name"))
        and bool(tool.get("requires_authentication", False))
    }


def _authenticated_cli_tools(discovered_tools: Iterable[dict[str, Any]]) -> set[str]:
    authenticated: set[str] = set()
    for tool in discovered_tools:
        if not isinstance(tool, dict):
            continue
        name = _tool_name(tool.get("name"))
        if not name:
            continue
        if not bool(tool.get("requires_authentication", False)):
            authenticated.add(name)
            continue
        if _tool_name(tool.get("authentication_state")) == "authenticated":
            authenticated.add(name)
    return authenticated


def _is_tool_capability(capability: Any) -> bool:
    return isinstance(capability, dict) and str(capability.get("type") or "").strip().casefold() == "tool"


def _provider_credentials(configured_capabilities: Iterable[Any]) -> dict[str, bool]:
    """Report configured cloud-provider credential presence without exposing values."""
    credentials: dict[str, bool] = {}
    for capability in configured_capabilities:
        if not isinstance(capability, dict):
            continue
        if str(capability.get("type") or "").strip().casefold() != "llm":
            continue
        provider = _tool_name(capability.get("provider"))
        env_name = _PROVIDER_CREDENTIAL_ENV.get(provider)
        if env_name:
            credentials[provider] = bool(os.environ.get(env_name, "").strip())
    return credentials


def attest_worker_capabilities(
    worker_config: dict[str, Any],
    discovered_tools: Iterable[dict[str, Any]],
    browser_attestation: dict[str, Any] | None = None,
    documentation_attestation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the effective worker config and a non-secret attestation report.

    A static manifest is policy, not proof that a program or native tool is usable.
    This runtime can attest CLI tools by checking the executable at startup, so every
    declared tool capability is replaced with the explicitly enabled, installed CLI
    tool set. Browser tooling is added only after its optional runtime verifies that
    Playwright and Chromium are present.
    """

    declared = deepcopy(worker_config)
    configured_capabilities = declared.get("capabilities")
    if not isinstance(configured_capabilities, list):
        configured_capabilities = []

    allowed_cli_tools = _configured_cli_tools(declared)
    discovered = [tool for tool in discovered_tools if isinstance(tool, dict)]
    installed_cli_tools = _installed_cli_tools(discovered)
    auth_required_cli_tools = _auth_required_cli_tools(discovered)
    authenticated_cli_tools = _authenticated_cli_tools(discovered)
    enabled_cli_tools = sorted(allowed_cli_tools & installed_cli_tools & authenticated_cli_tools)
    unavailable_cli_tools = sorted(allowed_cli_tools - installed_cli_tools)
    unauthenticated_cli_tools = sorted(
        allowed_cli_tools & installed_cli_tools & auth_required_cli_tools - authenticated_cli_tools
    )

    effective = deepcopy(declared)
    # Tool capabilities must be attested by a runtime implementation. Do not let a
    # manifest advertise browser, CLI, or custom tools that this node cannot serve.
    effective_capabilities = [
        deepcopy(capability)
        for capability in configured_capabilities
        if not _is_tool_capability(capability)
    ]
    if enabled_cli_tools:
        effective_capabilities.append(
            {
                "type": "tool",
                "provider": "cli",
                "models": enabled_cli_tools,
            }
        )
    browser_ready = bool((browser_attestation or {}).get("ready"))
    if browser_ready:
        effective_capabilities.append(
            {
                "type": "tool",
                "provider": "browser",
                "models": ["browser-ui"],
            }
        )
    documentation_ready = bool((documentation_attestation or {}).get("ready"))
    if documentation_ready:
        effective_capabilities.append(
            {
                "type": "tool",
                "provider": "documentation",
                "models": ["documentation-v1"],
            }
        )
    effective["capabilities"] = effective_capabilities

    tooling = effective.get("tooling")
    if not isinstance(tooling, dict):
        tooling = {}
    tooling["cli_tools"] = enabled_cli_tools
    effective["tooling"] = tooling

    report = {
        "configured_cli_tools": sorted(allowed_cli_tools),
        "installed_cli_tools": sorted(installed_cli_tools),
        "enabled_cli_tools": enabled_cli_tools,
        "unavailable_cli_tools": unavailable_cli_tools,
        "auth_required_cli_tools": sorted(allowed_cli_tools & auth_required_cli_tools),
        "unauthenticated_cli_tools": unauthenticated_cli_tools,
        "provider_credentials": _provider_credentials(configured_capabilities),
        "discarded_declared_tool_capabilities": sum(
            1 for capability in configured_capabilities if _is_tool_capability(capability)
        ),
        "browser": dict(browser_attestation or {"configured": False, "ready": False}),
        "documentation": dict(documentation_attestation or {"configured": False, "ready": False}),
    }
    return effective, report
