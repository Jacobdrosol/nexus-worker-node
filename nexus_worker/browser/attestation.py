"""Verify optional browser tooling before this node advertises it."""

from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nexus_worker.browser.inspector import (
    BrowserScopeError,
    normalized_relative_path,
    scoped_url,
    validated_page_url,
)


def browser_runtime_config(worker_config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the private browser configuration only when explicitly enabled."""

    tooling = worker_config.get("tooling")
    if not isinstance(tooling, dict):
        return None
    browser = tooling.get("browser")
    if not isinstance(browser, dict) or not bool(browser.get("enabled")):
        return None
    return dict(browser)


def _authenticated_session_check(runtime_config: dict[str, Any]) -> dict[str, Any] | None:
    """Validate an optional, non-secret browser-session readiness requirement."""

    raw_check = runtime_config.get("session_check")
    if raw_check is None:
        return None
    if not isinstance(raw_check, dict) or not bool(raw_check.get("required")):
        return None

    path = str(raw_check.get("path") or "").strip()
    selector = str(raw_check.get("authenticated_selector") or "").strip()
    if not path or not selector or len(selector) > 500:
        raise BrowserScopeError("invalid browser session check")
    target_url = scoped_url(
        str(runtime_config.get("base_url") or ""),
        path,
        runtime_config.get("allowed_paths"),
    )
    try:
        timeout_seconds = int(raw_check.get("timeout_seconds") or 15)
    except (TypeError, ValueError):
        timeout_seconds = 15
    return {
        "target_url": target_url,
        "authenticated_selector": selector,
        "timeout_ms": max(1_000, min(timeout_seconds * 1_000, 600_000)),
    }


def _attest_authenticated_session(
    runtime_config: dict[str, Any],
    session_check: dict[str, Any],
) -> bool:
    """Confirm the persisted profile can reach a protected page without exposing it."""

    from playwright.sync_api import sync_playwright

    profile_dir = str(runtime_config.get("user_data_dir") or "")
    target_url = str(session_check["target_url"])
    timeout_ms = int(session_check["timeout_ms"])
    expected_path = urlsplit(target_url).path
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=bool(runtime_config.get("headless", True)),
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            safe_page_url = validated_page_url(
                page.url,
                str(runtime_config.get("base_url") or ""),
                runtime_config.get("allowed_paths"),
            )
            if urlsplit(safe_page_url).path != expected_path:
                return False
            page.locator(str(session_check["authenticated_selector"])).first.wait_for(
                state="visible",
                timeout=timeout_ms,
            )
            return True
        finally:
            context.close()


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
        session_check = _authenticated_session_check(runtime_config)
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

    if session_check is not None:
        try:
            session_authenticated = _attest_authenticated_session(runtime_config, session_check)
        except BrowserScopeError:
            return {
                "configured": True,
                "ready": False,
                "reason": "browser_session_not_authenticated",
            }
        except Exception:
            return {
                "configured": True,
                "ready": False,
                "reason": "browser_session_check_failed",
            }
        if not session_authenticated:
            return {
                "configured": True,
                "ready": False,
                "reason": "browser_session_not_authenticated",
            }

    return {
        "configured": True,
        "ready": True,
        "browser": "chromium",
        "session_authenticated": session_check is not None,
    }
