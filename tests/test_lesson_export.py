import pytest

from nexus_worker.api.browser import BrowserLessonExportRequest
from nexus_worker.browser.inspector import BrowserScopeError
from nexus_worker.browser.lesson_export import (
    _export_session_check,
    validate_exported_lesson_identity,
    validate_lesson_export,
)


def _browser_config() -> dict:
    return {
        "base_url": "https://globeiq.example",
        "allowed_paths": ["/admin/courses"],
        "user_data_dir": "/private/browser-profile",
        "session_check": {
            "required": True,
            "path": "/admin/courses",
            "authenticated_selector": "h2",
        },
        "lesson_json_export": {
            "enabled": True,
            "allowed_actions": ["export json"],
            "allowed_course_ids": [57],
            "allowed_lesson_ids": [605001784],
        },
    }


def test_lesson_export_requires_exact_explicit_approval_and_scope():
    request = validate_lesson_export(
        _browser_config(),
        course_id=57,
        lesson_id=605001784,
        approved_read_only_actions=["export json"],
    )

    assert request["url"] == "https://globeiq.example/api/admin/lessons/605001784/builder-json"
    assert request["max_bytes"] == 256 * 1024


@pytest.mark.parametrize(
    ("course_id", "lesson_id", "actions", "message"),
    [
        (57, 605001784, [], "explicit approved"),
        (57, 605001784, ["export json", "inspect"], "only the approved"),
        (60, 605001784, ["export json"], "course is outside"),
        (57, 605001785, ["export json"], "lesson is outside"),
    ],
)
def test_lesson_export_rejects_unapproved_or_broad_requests(course_id, lesson_id, actions, message):
    with pytest.raises(BrowserScopeError, match=message):
        validate_lesson_export(
            _browser_config(),
            course_id=course_id,
            lesson_id=lesson_id,
            approved_read_only_actions=actions,
        )


def test_lesson_export_must_be_enabled_and_configured():
    config = _browser_config()
    config["lesson_json_export"]["enabled"] = False

    with pytest.raises(BrowserScopeError, match="not enabled"):
        validate_lesson_export(
            config,
            course_id=57,
            lesson_id=605001784,
            approved_read_only_actions=["export json"],
        )


def test_lesson_export_request_uses_the_public_read_only_action_field_name():
    request = BrowserLessonExportRequest.model_validate(
        {
            "course_id": 57,
            "lesson_id": 605001784,
            "approvedReadOnlyActions": ["export json"],
        }
    )

    assert request.approved_read_only_actions == ["export json"]


def test_lesson_export_requires_a_current_authenticated_browser_session_check():
    config = _browser_config()
    config.pop("session_check")

    with pytest.raises(BrowserScopeError, match="authenticated browser session check"):
        _export_session_check(config)


def test_lesson_export_requires_response_identity_to_match_its_scope():
    validate_exported_lesson_identity(
        {"lesson": {"Id": 605001784, "CourseId": 57}},
        course_id=57,
        lesson_id=605001784,
    )

    with pytest.raises(BrowserScopeError, match="does not match"):
        validate_exported_lesson_identity(
            {"lesson": {"Id": 605001784, "CourseId": 60}},
            course_id=57,
            lesson_id=605001784,
        )
