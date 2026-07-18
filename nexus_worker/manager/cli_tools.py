"""CLI tool discovery helpers for nexus_worker."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List


_KNOWN_CLI_TOOLS: list[dict[str, Any]] = [
    {
        "name": "claude",
        "version_args": ["--version"],
        "requires_approval": True,
        "approval_hints": [
            "Authenticate Claude Code or configure its node-local gateway credentials before enabling tasks.",
            "When using a gateway-backed model, configure the gateway and model selection only in the worker node environment.",
        ],
        "requires_authentication": True,
        "auth_check_args": ["auth", "status"],
    },
    {
        "name": "codex",
        "version_args": ["--version"],
        "requires_approval": True,
        "approval_hints": [
            "Run `codex login` on the worker node before enabling write-capable tasks.",
            "Review repository trust and sandbox settings before routing coding tasks here.",
        ],
        "requires_authentication": True,
        "auth_check_args": ["login", "status"],
    },
    {
        "name": "gh",
        "version_args": ["--version"],
        "requires_approval": True,
        "approval_hints": [
            "Run `gh auth login` or configure a GitHub token before using repository workflows.",
        ],
        "requires_authentication": True,
        "auth_check_args": ["auth", "status"],
    },
    {
        "name": "git",
        "version_args": ["--version"],
        "requires_approval": False,
        "approval_hints": [],
    },
    {
        "name": "python",
        "version_args": ["--version"],
        "requires_approval": False,
        "approval_hints": [],
    },
    {
        "name": "node",
        "version_args": ["--version"],
        "requires_approval": False,
        "approval_hints": [],
    },
    {
        "name": "npm",
        "version_args": ["--version"],
        "requires_approval": False,
        "approval_hints": [],
    },
    {
        "name": "docker",
        "version_args": ["--version"],
        "requires_approval": True,
        "approval_hints": [
            "Ensure the worker service account is allowed to access the Docker daemon before enabling Docker-backed tasks.",
        ],
    },
    {
        "name": "ollama",
        "version_args": ["--version"],
        "requires_approval": False,
        "approval_hints": [],
    },
]


def _read_version(command: str, version_args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            [command, *version_args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return None
    out = (proc.stdout or proc.stderr or "").strip()
    if not out:
        return None
    first_line = out.splitlines()[0].strip()
    return first_line[:200]


def _authentication_state(command: str, tool: Dict[str, Any]) -> str:
    """Return a non-secret authentication state for CLIs that require credentials."""

    if not bool(tool.get("requires_authentication", False)):
        return "not_required"
    gateway_state = _claude_gateway_authentication_state(command)
    if gateway_state is not None:
        return gateway_state
    args = [str(arg) for arg in tool.get("auth_check_args", []) if str(arg).strip()]
    if not args:
        return "unknown"
    try:
        proc = subprocess.run(
            [command, *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return "unknown"

    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    if command == "claude":
        try:
            payload = json.loads(proc.stdout or "{}")
            if isinstance(payload, dict) and payload.get("loggedIn") is True:
                return "authenticated"
            if isinstance(payload, dict) and payload.get("loggedIn") is False:
                return "not_authenticated"
        except (TypeError, ValueError):
            pass
    normalized = output.lower()
    if "not logged in" in normalized or "loggedin\": false" in normalized:
        return "not_authenticated"
    if proc.returncode == 0:
        return "authenticated"
    return "unknown"


def _claude_gateway_authentication_state(command: str) -> str | None:
    """Attest an explicitly configured private Claude gateway without exposing its token.

    Claude Code can use an Anthropic-compatible gateway instead of a vendor login.
    This path is opt-in and verifies the gateway's non-secret health response before
    treating the CLI as authenticated. A mere environment variable is never enough
    to enable the tool.
    """

    if command != "claude":
        return None
    enabled = os.environ.get("NEXUS_CLAUDE_GATEWAY_ATTESTATION", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    if not base_url or not token:
        return "not_authenticated"
    try:
        request = urllib.request.Request(
            f"{base_url}/health",
            headers={"X-Api-Key": token},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - operator-controlled endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return "not_authenticated"
    if isinstance(payload, dict) and payload.get("status") == "ok" and payload.get("ready") is True:
        return "authenticated"
    return "not_authenticated"


def discover_cli_tools() -> list[dict[str, Any]]:
    """Return detected CLI tools and their approval metadata."""
    discovered: list[dict[str, Any]] = []
    for tool in _KNOWN_CLI_TOOLS:
        path = shutil.which(str(tool["name"]))
        if not path:
            continue
        authentication_state = _authentication_state(str(tool["name"]), tool)
        discovered.append(
            {
                "name": tool["name"],
                "path": path,
                "version": _read_version(str(tool["name"]), list(tool.get("version_args") or ["--version"])),
                "requires_approval": bool(tool.get("requires_approval", False)),
                "requires_authentication": bool(tool.get("requires_authentication", False)),
                "authentication_state": authentication_state,
                "approval_hints": list(tool.get("approval_hints") or []),
            }
        )
    return discovered
