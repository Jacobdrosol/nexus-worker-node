import pytest

from nexus_worker.browser.inspector import (
    BrowserScopeError,
    ensure_allowed_path,
    normalized_relative_path,
    scoped_url,
    validated_page_url,
)


def test_browser_scope_accepts_exact_and_prefix_paths():
    allowed = ["/admin/courses", "/admin/lessons/*"]

    assert ensure_allowed_path("/admin/courses", allowed) == "/admin/courses"
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
