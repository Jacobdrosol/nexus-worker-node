"""Verify optional browser tooling before this node advertises it."""

from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
from typing import Any

from nexus_worker.browser.inspector import BrowserScopeError, normalized_relative_path, scoped_url


def browser_runtime_config(worker_config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the private browser configuration only when explicitly enabled."""

    tooling = worker_config.get("tooling")
    if not isinstance(tooling, dict):
        return None
    browser = tooling.get("browser")
    if not isinstance(browser, dict) or not bool(browser.get("enabled")):
        return None
    return dict(browser)


def attest_browser_runtime(worker_config: dict[str, Any]) -> dict[str, Any]:
    """Return non-secret evidence that an enabled browser runtime is usable."""

    runtime_config = browser_runtime_config(worker_config)
    if runtime_config is None:
        return {"configured": False, "ready": False}

    allowed_paths = runtime_config.get("allowed_paths")
    profile_dir = str(runtime_config.get("user_data_dir") or "")
    request_token_env = str(runtime_config.get("request_token_env") or "").strip()
    try:
        if not profile_dir or not isinstance(allowed_paths, list) or not allowed_paths:
            raise BrowserScopeError("missing browser scope or persistent profile")
        for allowed_path in allowed_paths:
            candidate = str(allowed_path or "")
            if candidate.endswith("/*"):
                candidate = candidate[:-1]
            normalized = normalized_relative_path(candidate)
            if normalized == "/":
                raise BrowserScopeError("unbounded browser scope")
        scoped_url(str(runtime_config.get("base_url") or ""), candidate, allowed_paths)
    except BrowserScopeError:
        return {
            "configured": True,
            "ready": False,
            "reason": "browser_configuration_invalid",
        }

    if not request_token_env or not os.environ.get(request_token_env, "").strip():
        return {
            "configured": True,
            "ready": False,
            "reason": "browser_request_token_missing",
        }

    if find_spec("playwright") is None:
        return {
            "configured": True,
            "ready": False,
            "reason": "playwright_not_installed",
        }

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable_path = Path(playwright.chromium.executable_path)
    except Exception:
        return {
            "configured": True,
            "ready": False,
            "reason": "playwright_driver_unavailable",
        }

    if not executable_path.is_file():
        return {
            "configured": True,
            "ready": False,
            "reason": "chromium_not_installed",
        }

    return {
        "configured": True,
        "ready": True,
        "browser": "chromium",
    }
