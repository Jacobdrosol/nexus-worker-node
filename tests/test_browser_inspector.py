import pytest

from nexus_worker.browser.inspector import (
    BrowserScopeError,
    browser_timeout_ms,
    ensure_allowed_path,
    loading_shell_present,
    normalized_relative_path,
    render_ready_selector,
    render_ready_timeout_ms,
    scoped_url,
    validated_page_url,
)


def test_browser_scope_accepts_exact_and_prefix_paths():
    allowed = ["/admin/courses", "/admin/lessons/*"]

    assert ensure_allowed_path("/admin/courses", allowed) == "/admin/courses"
    assert ensure_allowed_path("/admin/lessons", allowed) == "/admin/lessons"
    assert ensure_allowed_path("/admin/lessons/42", allowed) == "/admin/lessons/42"


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.example/admin",
        "//attacker.example/admin",
        "/admin/../secrets",
        "/admin/%2e%2e/secrets",
        "/admin?next=https://attacker.example",
        "/admin#fragment",
        "admin/courses",
    ],
)
def test_browser_scope_rejects_unsafe_paths(path):
    with pytest.raises(BrowserScopeError):
        normalized_relative_path(path)


def test_browser_scope_rejects_paths_outside_allowed_scope():
    with pytest.raises(BrowserScopeError, match="outside"):
        ensure_allowed_path("/admin/users", ["/admin/courses"])
    with pytest.raises(BrowserScopeError, match="outside"):
        ensure_allowed_path("/admin/courses-admin", ["/admin/courses/*"])


def test_browser_scope_joins_only_to_configured_origin():
    assert scoped_url(
        "https://app.example/root",
        "/admin/courses",
        ["/admin/*"],
    ) == "https://app.example/root/admin/courses"


def test_browser_scope_keeps_redirects_on_configured_origin_and_strips_query_data():
    assert validated_page_url(
        "https://app.example/root/admin/courses?session=private#section",
        "https://app.example/root",
        ["/admin/*"],
    ) == "https://app.example/root/admin/courses"


def test_browser_scope_rejects_redirects_outside_configured_origin():
    with pytest.raises(BrowserScopeError, match="left the configured origin"):
        validated_page_url(
            "https://attacker.example/admin/courses",
            "https://app.example",
            ["/admin/*"],
        )


def test_browser_scope_rejects_unbounded_root_scope():
    with pytest.raises(BrowserScopeError, match="every path"):
        ensure_allowed_path("/admin/courses", ["/*"])


def test_browser_render_ready_wait_is_optional_and_bounded():
    assert render_ready_timeout_ms({}) == 0
    assert render_ready_timeout_ms({"render_ready_timeout_seconds": "7"}) == 7_000
    assert render_ready_timeout_ms({"render_ready_timeout_seconds": 999}) == 300_000
    assert render_ready_timeout_ms({"render_ready_timeout_seconds": -1}) == 0


def test_browser_readiness_requires_a_render_wait_when_configured():
    assert render_ready_timeout_ms({"render_ready_timeout_seconds": 90}) == 90_000


def test_browser_loading_shell_detection_rejects_transient_client_states():
    assert loading_shell_present("Application content is loading.")
    assert loading_shell_present("Authorizing")
    assert loading_shell_present("Loading...")
    assert not loading_shell_present("Course Management")


def test_browser_operation_timeout_uses_seconds_with_bounded_legacy_compatibility():
    assert browser_timeout_ms({}) == 30_000
    assert browser_timeout_ms({"timeout_seconds": 120}) == 120_000
    assert browser_timeout_ms({"timeout_seconds": 300_000}) == 300_000
    assert browser_timeout_ms({"timeout_seconds": 999_999}) == 600_000


def test_browser_render_ready_selector_is_optional_and_bounded():
    assert render_ready_selector({}) == ""
    assert render_ready_selector({"render_ready_selector": "h1, h2"}) == "h1, h2"
    with pytest.raises(BrowserScopeError, match="selector"):
        render_ready_selector({"render_ready_selector": "x" * 501})
    assert render_ready_selector(
        {"render_ready_selectors": {"/admin/courses/57/lessons": "h2:has-text('Lesson Manager')"}},
        "/admin/courses/57/lessons",
    ) == "h2:has-text('Lesson Manager')"
    with pytest.raises(BrowserScopeError, match="object"):
        render_ready_selector({"render_ready_selectors": ["h2"]}, "/admin/courses/57/lessons")
