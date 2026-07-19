"""Manual-only authentication bootstrap for an isolated browser profile."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from nexus_worker.browser.attestation import attest_browser_runtime, browser_runtime_config
from nexus_worker.browser.inspector import BrowserScopeError, scoped_url


class BrowserSessionBootstrapError(RuntimeError):
    """Raised when an operator-requested browser login cannot be completed safely."""


@dataclass(frozen=True)
class BrowserSessionBootstrapSettings:
    login_url: str
    profile_dir: str
    username_selector: str
    password_selector: str
    submit_selector: str
    timeout_ms: int
    headless: bool
    username: str = field(repr=False)
    password: str = field(repr=False)


def _required_text(config: dict[str, Any], field_name: str) -> str:
    value = str(config.get(field_name) or "").strip()
    if not value:
        raise BrowserSessionBootstrapError(f"Browser session bootstrap requires '{field_name}'")
    return value


def _bounded_timeout_seconds(value: Any) -> int:
    try:
        seconds = int(value or 30)
    except (TypeError, ValueError):
        seconds = 30
    return max(5, min(seconds, 600))


def browser_session_bootstrap_settings(
    worker_config: dict[str, Any],
) -> BrowserSessionBootstrapSettings:
    """Validate a private manual-login configuration without logging its secrets."""

    runtime_config = browser_runtime_config(worker_config)
    if runtime_config is None:
        raise BrowserSessionBootstrapError("Browser tooling is not enabled for this worker")

    raw_bootstrap = runtime_config.get("session_bootstrap")
    if not isinstance(raw_bootstrap, dict) or not bool(raw_bootstrap.get("enabled")):
        raise BrowserSessionBootstrapError("Browser session bootstrap is not enabled for this worker")

    raw_session_check = runtime_config.get("session_check")
    if not isinstance(raw_session_check, dict) or not bool(raw_session_check.get("required")):
        raise BrowserSessionBootstrapError(
            "Browser session bootstrap requires a mandatory authenticated session check"
        )

    profile_dir = _required_text(runtime_config, "user_data_dir")
    login_path = _required_text(raw_bootstrap, "login_path")
    try:
        login_url = scoped_url(
            str(runtime_config.get("base_url") or ""),
            login_path,
            runtime_config.get("allowed_paths"),
        )
    except BrowserScopeError as exc:
        raise BrowserSessionBootstrapError("Browser login path is outside the declared scope") from exc

    username_selector = _required_text(raw_bootstrap, "username_selector")
    password_selector = _required_text(raw_bootstrap, "password_selector")
    submit_selector = _required_text(raw_bootstrap, "submit_selector")
    if any(len(selector) > 500 for selector in (username_selector, password_selector, submit_selector)):
        raise BrowserSessionBootstrapError("Browser login selector exceeds the supported length")

    username_env = _required_text(raw_bootstrap, "username_env")
    password_env = _required_text(raw_bootstrap, "password_env")
    username = os.environ.get(username_env, "").strip()
    password = os.environ.get(password_env, "").strip()
    if not username or not password:
        raise BrowserSessionBootstrapError(
            "Browser login credentials are missing from the configured private environment"
        )

    return BrowserSessionBootstrapSettings(
        login_url=login_url,
        profile_dir=profile_dir,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        timeout_ms=_bounded_timeout_seconds(raw_bootstrap.get("timeout_seconds")) * 1_000,
        headless=bool(runtime_config.get("headless", True)),
        username=username,
        password=password,
    )


def bootstrap_browser_session(worker_config: dict[str, Any]) -> dict[str, Any]:
    """Log into a pre-scoped site once and attest the persisted browser session.

    This function is intentionally reachable only through the explicit CLI command.
    It never starts as part of the worker service or a scheduled task.
    """

    settings = browser_session_bootstrap_settings(worker_config)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=settings.profile_dir,
                headless=settings.headless,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(settings.login_url, wait_until="domcontentloaded", timeout=settings.timeout_ms)
                page.locator(settings.username_selector).fill(settings.username, timeout=settings.timeout_ms)
                page.locator(settings.password_selector).fill(settings.password, timeout=settings.timeout_ms)
                page.locator(settings.submit_selector).click(timeout=settings.timeout_ms)
                try:
                    page.wait_for_url(
                        lambda url: url != settings.login_url,
                        timeout=settings.timeout_ms,
                    )
                except PlaywrightTimeoutError:
                    # The subsequent bounded session check distinguishes an invalid login from a slow route change.
                    pass
            finally:
                context.close()
    except BrowserSessionBootstrapError:
        raise
    except Exception as exc:
        raise BrowserSessionBootstrapError("Browser login could not be completed") from exc

    attestation = attest_browser_runtime(worker_config)
    if not attestation.get("ready") or not attestation.get("session_authenticated"):
        raise BrowserSessionBootstrapError("Browser login did not produce an authenticated session")
    return {
        "status": "ready",
        "browser": str(attestation.get("browser") or "chromium"),
        "session_authenticated": True,
    }
