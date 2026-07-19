"""Narrow, authenticated read-only lesson JSON exports."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nexus_worker.browser.inspector import (
    BrowserScopeError,
    _bounded_int,
    browser_timeout_ms,
    scoped_url,
    validated_page_url,
)


_LESSON_EXPORT_ALLOWED_PATHS = ["/api/admin/lessons/*"]
_REQUIRED_ACTION = "export json"
_MAX_EXPORT_BYTES = 512 * 1024


def _positive_id_set(value: Any, *, field_name: str) -> set[int]:
    if not isinstance(value, list) or not value:
        raise BrowserScopeError(f"Lesson export requires configured {field_name}")
    parsed: set[int] = set()
    for item in value:
        try:
            candidate = int(item)
        except (TypeError, ValueError) as exc:
            raise BrowserScopeError(f"Lesson export has an invalid {field_name}") from exc
        if candidate < 1:
            raise BrowserScopeError(f"Lesson export has an invalid {field_name}")
        parsed.add(candidate)
    return parsed


def validate_lesson_export(
    browser_config: dict[str, Any],
    *,
    course_id: int,
    lesson_id: int,
    approved_read_only_actions: list[str],
) -> dict[str, Any]:
    """Validate one explicitly approved export against an intentionally tiny scope."""

    if course_id < 1 or lesson_id < 1:
        raise BrowserScopeError("Lesson export requires positive course and lesson ids")
    if not isinstance(approved_read_only_actions, list):
        raise BrowserScopeError("Lesson export requires an explicit approved read-only action")
    normalized_actions = {str(item or "").strip().lower() for item in approved_read_only_actions}
    if not normalized_actions or normalized_actions == {""}:
        raise BrowserScopeError("Lesson export requires an explicit approved read-only action")
    if normalized_actions != {_REQUIRED_ACTION}:
        raise BrowserScopeError("Lesson export requires only the approved read-only action 'export json'")

    runtime = browser_config.get("lesson_json_export")
    if not isinstance(runtime, dict) or not bool(runtime.get("enabled")):
        raise BrowserScopeError("Lesson JSON export is not enabled on this worker")
    configured_actions = runtime.get("allowed_actions")
    if not isinstance(configured_actions, list) or _REQUIRED_ACTION not in {
        str(item or "").strip().lower() for item in configured_actions
    }:
        raise BrowserScopeError("Lesson JSON export is not allowed on this worker")

    if course_id not in _positive_id_set(runtime.get("allowed_course_ids"), field_name="allowed course ids"):
        raise BrowserScopeError("Lesson export course is outside the configured scope")
    if lesson_id not in _positive_id_set(runtime.get("allowed_lesson_ids"), field_name="allowed lesson ids"):
        raise BrowserScopeError("Lesson export lesson is outside the configured scope")

    profile_dir = str(browser_config.get("user_data_dir") or "")
    if not profile_dir:
        raise BrowserScopeError("Lesson export requires a persistent browser profile")

    return {
        "course_id": course_id,
        "lesson_id": lesson_id,
        "url": scoped_url(
            str(browser_config.get("base_url") or ""),
            f"/api/admin/lessons/{lesson_id}/builder-json",
            _LESSON_EXPORT_ALLOWED_PATHS,
        ),
        "max_bytes": _bounded_int(
            runtime.get("max_bytes"),
            default=256 * 1024,
            minimum=1_024,
            maximum=_MAX_EXPORT_BYTES,
        ),
    }


def _export_session_check(browser_config: dict[str, Any]) -> dict[str, str]:
    """Require a current authenticated UI session before using its cookies for export."""

    raw_check = browser_config.get("session_check")
    if not isinstance(raw_check, dict) or not bool(raw_check.get("required")):
        raise BrowserScopeError("Lesson export requires an authenticated browser session check")
    path = str(raw_check.get("path") or "").strip()
    selector = str(raw_check.get("authenticated_selector") or "").strip()
    if not path or not selector or len(selector) > 500:
        raise BrowserScopeError("Lesson export requires a valid authenticated browser session check")
    return {
        "url": scoped_url(
            str(browser_config.get("base_url") or ""),
            path,
            browser_config.get("allowed_paths"),
        ),
        "selector": selector,
    }


def _required_export_identity(metadata: dict[str, Any], names: tuple[str, ...], label: str) -> int:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, bool):
            continue
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            continue
        if candidate > 0:
            return candidate
    raise BrowserScopeError(f"Lesson export did not return a valid {label}")


def validate_exported_lesson_identity(
    lesson_json: dict[str, Any] | list[Any],
    *,
    course_id: int,
    lesson_id: int,
) -> None:
    """Reject JSON that does not prove it belongs to the requested lesson scope."""

    if not isinstance(lesson_json, dict):
        raise BrowserScopeError("Lesson export did not return lesson metadata")
    metadata = lesson_json.get("lesson")
    if not isinstance(metadata, dict):
        raise BrowserScopeError("Lesson export did not return lesson metadata")
    exported_lesson_id = _required_export_identity(metadata, ("Id", "id", "LessonId", "lessonId"), "lesson id")
    exported_course_id = _required_export_identity(metadata, ("CourseId", "courseId"), "course id")
    if exported_lesson_id != lesson_id or exported_course_id != course_id:
        raise BrowserScopeError("Lesson export identity does not match the configured scope")


async def export_lesson_builder_json(
    browser_config: dict[str, Any],
    *,
    course_id: int,
    lesson_id: int,
    approved_read_only_actions: list[str],
) -> dict[str, Any]:
    """Return one configured lesson's builder JSON without interacting with the UI."""

    from playwright.async_api import async_playwright

    request = validate_lesson_export(
        browser_config,
        course_id=course_id,
        lesson_id=lesson_id,
        approved_read_only_actions=approved_read_only_actions,
    )
    session_check = _export_session_check(browser_config)
    timeout_ms = browser_timeout_ms(browser_config)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_config["user_data_dir"]),
            headless=bool(browser_config.get("headless", True)),
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(session_check["url"], wait_until="domcontentloaded", timeout=timeout_ms)
            validated_page_url(
                page.url,
                str(browser_config.get("base_url") or ""),
                browser_config.get("allowed_paths"),
            )
            await page.locator(session_check["selector"]).first.wait_for(state="visible", timeout=timeout_ms)
            try:
                result = await page.evaluate(
                    """
                    async ({ url, maxBytes }) => {
                        const response = await fetch(url, {
                            credentials: 'same-origin',
                            method: 'GET',
                            redirect: 'follow',
                        });
                        const declaredLength = Number(response.headers.get('content-length') || '0');
                        if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
                            return { url: response.url, status: response.status, tooLarge: true };
                        }
                        if (!response.body) {
                            return { url: response.url, status: response.status, noBody: true };
                        }
                        const reader = response.body.getReader();
                        const chunks = [];
                        let size = 0;
                        while (true) {
                            const next = await reader.read();
                            if (next.done) break;
                            size += next.value.byteLength;
                            if (size > maxBytes) {
                                await reader.cancel();
                                return { url: response.url, status: response.status, tooLarge: true };
                            }
                            chunks.push(next.value);
                        }
                        const bytes = new Uint8Array(size);
                        let offset = 0;
                        for (const chunk of chunks) {
                            bytes.set(chunk, offset);
                            offset += chunk.byteLength;
                        }
                        return {
                            url: response.url,
                            status: response.status,
                            contentType: response.headers.get('content-type') || '',
                            text: new TextDecoder('utf-8', { fatal: true }).decode(bytes),
                        };
                    }
                    """,
                    {"url": request["url"], "maxBytes": request["max_bytes"]},
                )
            except Exception as exc:
                raise BrowserScopeError("Lesson export request failed") from exc
            if not isinstance(result, dict):
                raise BrowserScopeError("Lesson export returned an invalid response")
            safe_url = validated_page_url(
                str(result.get("url") or ""),
                str(browser_config.get("base_url") or ""),
                _LESSON_EXPORT_ALLOWED_PATHS,
            )
            if result.get("tooLarge"):
                raise BrowserScopeError("Lesson export exceeded the configured response limit")
            if result.get("noBody") or int(result.get("status") or 0) != 200:
                raise BrowserScopeError("Lesson export did not return the requested JSON")
            content_type = str(result.get("contentType") or "").lower()
            if "application/json" not in content_type:
                raise BrowserScopeError("Lesson export did not return JSON content")
            raw_json = str(result.get("text") or "")
            if len(raw_json.encode("utf-8")) > request["max_bytes"]:
                raise BrowserScopeError("Lesson export exceeded the configured response limit")
            try:
                lesson_json = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise BrowserScopeError("Lesson export returned invalid JSON") from exc
            if not isinstance(lesson_json, (dict, list)):
                raise BrowserScopeError("Lesson export returned an unsupported JSON shape")
            validate_exported_lesson_identity(
                lesson_json,
                course_id=request["course_id"],
                lesson_id=request["lesson_id"],
            )
            return {
                "action": _REQUIRED_ACTION,
                "course_id": request["course_id"],
                "lesson_id": request["lesson_id"],
                "url": safe_url,
                "content_bytes": len(raw_json.encode("utf-8")),
                "content_sha256": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
                "lesson_json": lesson_json,
            }
        finally:
            await context.close()
