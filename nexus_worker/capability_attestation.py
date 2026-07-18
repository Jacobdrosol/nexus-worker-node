"""Build the capability record a worker can truthfully register with NexusAI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


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


def _is_tool_capability(capability: Any) -> bool:
    return isinstance(capability, dict) and str(capability.get("type") or "").strip().casefold() == "tool"


def attest_worker_capabilities(
    worker_config: dict[str, Any],
    discovered_tools: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the effective worker config and a non-secret attestation report.

    A static manifest is policy, not proof that a program or native tool is usable.
    This runtime can attest CLI tools by checking the executable at startup, so every
    declared tool capability is replaced with the explicitly enabled, installed CLI
    tool set. Native tools should add their own runtime attestor before registration.
    """

    declared = deepcopy(worker_config)
    configured_capabilities = declared.get("capabilities")
    if not isinstance(configured_capabilities, list):
        configured_capabilities = []

    allowed_cli_tools = _configured_cli_tools(declared)
    installed_cli_tools = _installed_cli_tools(discovered_tools)
    enabled_cli_tools = sorted(allowed_cli_tools & installed_cli_tools)
    unavailable_cli_tools = sorted(allowed_cli_tools - installed_cli_tools)

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
        "discarded_declared_tool_capabilities": sum(
            1 for capability in configured_capabilities if _is_tool_capability(capability)
        ),
    }
    return effective, report
