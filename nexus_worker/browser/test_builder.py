"""Constrained browser actions for GlobeIQ's Admin Test Builder."""

from __future__ import annotations

from typing import Any

from nexus_worker.browser.inspector import BrowserScopeError, browser_timeout_ms, scoped_url, validated_page_url


_ALLOWED_ACTIONS = {"save_configuration", "build_from_banks"}
_TEST_BUILDER_PATH = "/admin/courses/{course_id}/lessons/{lesson_id}/test"


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        candidate = default
    return max(minimum, min(candidate, maximum))


def validate_test_builder_action(
    browser_config: dict[str, Any],
    *,
    action: str,
    mode: str,
    confirmation: str,
    course_id: int,
    lesson_id: int,
    banks: list[dict[str, Any]],
    acknowledge_attempt_reset: bool,
) -> dict[str, Any]:
    """Validate the fixed Test Builder contract before a browser is launched."""

    normalized_action = str(action or "").strip().lower()
    if normalized_action == "publish":
        raise BrowserScopeError("Publishing is prohibited for browser workers")
    if normalized_action not in _ALLOWED_ACTIONS:
        raise BrowserScopeError("Unsupported Test Builder action")
    if str(mode or "").strip().lower() != "draft":
        raise BrowserScopeError("Test Builder actions are limited to draft mode")
    if str(confirmation or "").strip() != f"approved:test-builder:{normalized_action}":
        raise BrowserScopeError("Test Builder action requires explicit confirmation")
    if course_id <= 0 or lesson_id <= 0:
        raise BrowserScopeError("Test Builder actions require positive course and lesson ids")
    if not banks or len(banks) > 12:
        raise BrowserScopeError("Test Builder actions require between one and twelve banks")

    runtime = browser_config.get("assessment_test_builder")
    if not isinstance(runtime, dict) or not bool(runtime.get("enabled")):
        raise BrowserScopeError("Test Builder actions are not enabled on this worker")
    configured_actions = runtime.get("allowed_actions")
    if not isinstance(configured_actions, list) or normalized_action not in configured_actions:
        raise BrowserScopeError("Test Builder action is not allowed on this worker")
    if normalized_action == "build_from_banks" and not acknowledge_attempt_reset:
        raise BrowserScopeError("Building from banks requires an explicit attempt-reset acknowledgement")

    sanitized_banks: list[dict[str, Any]] = []
    for bank in banks:
        if not isinstance(bank, dict):
            raise BrowserScopeError("Test Builder bank selections must be objects")
        name = str(bank.get("name") or "").strip()
        if not name or len(name) > 240:
            raise BrowserScopeError("Test Builder bank names must be between one and 240 characters")
        counts = {
            difficulty: _bounded_int(bank.get(difficulty), default=0, minimum=0, maximum=100)
            for difficulty in ("easy", "medium", "hard", "apply")
        }
        if sum(counts.values()) <= 0:
            raise BrowserScopeError("Each Test Builder bank must request at least one question")
        sanitized_banks.append({"name": name, **counts})

    return {"action": normalized_action, "banks": sanitized_banks}


async def execute_test_builder_action(
    browser_config: dict[str, Any],
    *,
    action: str,
    mode: str,
    confirmation: str,
    course_id: int,
    lesson_id: int,
    title: str | None,
    pass_threshold_pct: int,
    time_limit_seconds: int | None,
    allow_review: bool,
    banks: list[dict[str, Any]],
    acknowledge_attempt_reset: bool = False,
) -> dict[str, Any]:
    """Run one named Test Builder action without allowing arbitrary browser input."""

    request = validate_test_builder_action(
        browser_config,
        action=action,
        mode=mode,
        confirmation=confirmation,
        course_id=course_id,
        lesson_id=lesson_id,
        banks=banks,
        acknowledge_attempt_reset=acknowledge_attempt_reset,
    )
    profile_dir = str(browser_config.get("user_data_dir") or "")
    if not profile_dir:
        raise BrowserScopeError("Test Builder actions require a persistent profile directory")
    timeout_ms = browser_timeout_ms(browser_config)
    target_path = _TEST_BUILDER_PATH.format(course_id=course_id, lesson_id=lesson_id)
    target_url = scoped_url(
        str(browser_config.get("base_url") or ""),
        target_path,
        browser_config.get("allowed_paths"),
    )

    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=bool(browser_config.get("headless", True)),
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            safe_page_url = validated_page_url(
                page.url,
                str(browser_config.get("base_url") or ""),
                browser_config.get("allowed_paths"),
            )
            save_button = page.get_by_test_id("test-builder-save-configuration")
            await save_button.wait_for(state="visible", timeout=timeout_ms)
            if await save_button.is_disabled():
                raise BrowserScopeError("Test Builder is read-only; published lessons cannot be edited")

            if title is not None:
                await page.get_by_test_id("test-builder-title").fill(title)
            await page.get_by_test_id("test-builder-pass-threshold").fill(str(pass_threshold_pct))
            time_limit_input = page.get_by_test_id("test-builder-time-limit")
            await time_limit_input.fill("" if time_limit_seconds is None else str(time_limit_seconds))
            review_input = page.get_by_test_id("test-builder-allow-review")
            if await review_input.is_checked() != allow_review:
                await review_input.click()

            for index, bank in enumerate(request["banks"]):
                if index:
                    await page.get_by_test_id("test-builder-add-bank").click()
                await page.get_by_test_id(f"test-builder-bank-{index}").fill(bank["name"])
                for difficulty in ("easy", "medium", "hard", "apply"):
                    await page.get_by_test_id(f"test-builder-{difficulty}-{index}").fill(
                        str(bank[difficulty])
                    )

            button_id = (
                "test-builder-save-configuration"
                if request["action"] == "save_configuration"
                else "test-builder-build"
            )
            status = page.get_by_test_id("test-builder-status")
            await page.get_by_test_id(button_id).click()
            try:
                await status.wait_for(state="visible", timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                raise BrowserScopeError("Test Builder action did not return a status") from exc
            status_text = (await status.inner_text()).strip()
            expected_status = (
                "Test configuration saved successfully"
                if request["action"] == "save_configuration"
                else "Test configuration and question selection built"
            )
            if status_text != expected_status:
                raise BrowserScopeError(f"Test Builder action failed: {status_text or 'unknown error'}")
            return {
                "action": request["action"],
                "mode": "draft",
                "course_id": course_id,
                "lesson_id": lesson_id,
                "url": safe_page_url,
                "bank_count": len(request["banks"]),
                "requested_question_count": sum(
                    bank["easy"] + bank["medium"] + bank["hard"] + bank["apply"]
                    for bank in request["banks"]
                ),
                "status": status_text,
            }
        finally:
            await context.close()
