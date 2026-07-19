"""Bounded, read-only browser inspection for a pre-scoped website."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


class BrowserScopeError(ValueError):
    """Raised when a browser request is outside its configured read-only scope."""


def loading_shell_present(text: str) -> bool:
    """Detect the transient client-rendering states that are not usable evidence."""

    return any(marker in text for marker in ("Application content is loading.", "Authorizing", "Loading..."))


def normalized_relative_path(path: str) -> str:
    """Reject absolute URLs, traversal, queries, and fragments before navigation."""

    parts = urlsplit(str(path or ""))
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise BrowserScopeError("Browser inspection accepts only a relative path")
    decoded = unquote(parts.path)
    if not decoded.startswith("/") or decoded.startswith("//") or "\\" in decoded:
        raise BrowserScopeError("Browser inspection path must begin with a single slash")
    segments = decoded.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise BrowserScopeError("Browser inspection path cannot contain traversal segments")
    return decoded


def _normalized_allowed_path(value: Any) -> tuple[str, bool]:
    raw = str(value or "")
    is_prefix = raw.endswith("/*")
    path = raw[:-1] if is_prefix else raw
    normalized = normalized_relative_path(path)
    if normalized == "/":
        raise BrowserScopeError("Browser inspection scope cannot allow every path")
    return normalized, is_prefix


def ensure_allowed_path(path: str, allowed_paths: Any) -> str:
    """Return a normalized path only when it is included in the declared scope."""

    normalized = normalized_relative_path(path)
    if not isinstance(allowed_paths, list) or not allowed_paths:
        raise BrowserScopeError("Browser inspection has no configured path scope")
    for allowed in allowed_paths:
        allowed_path, is_prefix = _normalized_allowed_path(allowed)
        prefix_root = allowed_path.rstrip("/")
        if normalized == allowed_path or (
            is_prefix
            and (normalized == prefix_root or normalized.startswith(allowed_path))
        ):
            return normalized
    raise BrowserScopeError("Browser inspection path is outside the configured scope")


def scoped_url(base_url: str, path: str, allowed_paths: Any) -> str:
    """Join a local path to the configured origin without permitting an origin escape."""

    base = urlsplit(str(base_url or ""))
    if base.scheme not in {"http", "https"} or not base.netloc or base.username or base.password:
        raise BrowserScopeError("Browser inspection requires a valid configured base URL")
    if base.query or base.fragment:
        raise BrowserScopeError("Browser inspection base URL cannot include query or fragment")
    try:
        _ = base.port
    except ValueError as exc:
        raise BrowserScopeError("Browser inspection base URL has an invalid port") from exc
    if any(segment in {".", ".."} for segment in unquote(base.path).split("/")):
        raise BrowserScopeError("Browser inspection base URL cannot contain traversal segments")
    normalized = ensure_allowed_path(path, allowed_paths)
    origin = urlunsplit((base.scheme, base.netloc, base.path.rstrip("/") + "/", "", ""))
    target = urlsplit(urljoin(origin, normalized.lstrip("/")))
    if target.scheme != base.scheme or target.netloc != base.netloc:
        raise BrowserScopeError("Browser inspection target escaped the configured origin")
    return urlunsplit((target.scheme, target.netloc, target.path, "", ""))


def validated_page_url(final_url: str, base_url: str, allowed_paths: Any) -> str:
    """Keep redirects on the configured origin and return a non-sensitive URL."""

    base = urlsplit(str(base_url or ""))
    final = urlsplit(str(final_url or ""))
    if final.scheme != base.scheme or final.netloc != base.netloc:
        raise BrowserScopeError("Browser inspection navigation left the configured origin")
    base_path = base.path.rstrip("/")
    if base_path:
        if final.path == base_path:
            relative_path = "/"
        elif final.path.startswith(base_path + "/"):
            relative_path = final.path[len(base_path) :]
        else:
            raise BrowserScopeError("Browser inspection navigation left the configured base path")
    else:
        relative_path = final.path
    ensure_allowed_path(relative_path, allowed_paths)
    return urlunsplit((final.scheme, final.netloc, final.path, "", ""))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        candidate = default
    return max(minimum, min(candidate, maximum))


def render_ready_timeout_ms(browser_config: dict[str, Any]) -> int:
    """Return an optional, bounded wait for a client-rendered page to leave its shell."""

    seconds = _bounded_int(
        browser_config.get("render_ready_timeout_seconds"),
        default=0,
        minimum=0,
        maximum=300,
    )
    return seconds * 1_000


def browser_timeout_ms(browser_config: dict[str, Any]) -> int:
    """Return a bounded browser-operation timeout from the historical seconds setting."""

    try:
        configured = int(browser_config.get("timeout_seconds") or 30)
    except (TypeError, ValueError):
        configured = 30
    # Early private configurations used milliseconds despite the field name.
    # Treat normal values as seconds while retaining that bounded compatibility.
    milliseconds = configured * 1_000 if configured <= 1_200 else configured
    return max(1_000, min(milliseconds, 600_000))


def render_ready_selector(browser_config: dict[str, Any], path: str = "") -> str:
    """Return an optional bounded selector that proves a client-rendered page is ready."""

    scoped_selectors = browser_config.get("render_ready_selectors")
    if scoped_selectors is not None and not isinstance(scoped_selectors, dict):
        raise BrowserScopeError("Browser render-ready selectors must be an object")
    selector = ""
    if isinstance(scoped_selectors, dict) and path:
        selector = str(scoped_selectors.get(path) or "").strip()
    if not selector:
        selector = str(browser_config.get("render_ready_selector") or "").strip()
    if len(selector) > 500:
        raise BrowserScopeError("Browser render-ready selector exceeds the supported length")
    return selector


async def inspect_page(
    browser_config: dict[str, Any],
    *,
    path: str,
    text_limit: int = 12000,
    element_limit: int = 40,
) -> dict[str, Any]:
    """Navigate once and return bounded visible page evidence without interacting."""

    from playwright.async_api import async_playwright

    target_url = scoped_url(
        str(browser_config.get("base_url") or ""),
        path,
        browser_config.get("allowed_paths"),
    )
    profile_dir = str(browser_config.get("user_data_dir") or "")
    if not profile_dir:
        raise BrowserScopeError("Browser inspection requires a persistent profile directory")
    timeout_ms = browser_timeout_ms(browser_config)
    ready_timeout_ms = render_ready_timeout_ms(browser_config)
    ready_selector = render_ready_selector(browser_config, path)
    safe_text_limit = _bounded_int(text_limit, default=12000, minimum=100, maximum=32000)
    safe_element_limit = _bounded_int(element_limit, default=40, minimum=1, maximum=100)

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
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
            except Exception:
                pass
            if ready_timeout_ms:
                try:
                    await page.wait_for_timeout(min(750, ready_timeout_ms))
                    if ready_selector:
                        await page.locator(ready_selector).first.wait_for(
                            state="visible",
                            timeout=ready_timeout_ms,
                        )
                    else:
                        await page.wait_for_function(
                            """
                                () => {
                                    const text = (document.body?.innerText || '').trim();
                                    return text.length > 0
                                    && !text.includes('Application content is loading.')
                                    && !text.includes('Authorizing')
                                    && !text.includes('Loading...');
                            }
                            """,
                            timeout=ready_timeout_ms,
                        )
                except Exception as exc:
                    raise BrowserScopeError(
                        "Browser page did not finish rendering within the configured read-only wait"
                    ) from exc
            safe_page_url = validated_page_url(
                page.url,
                str(browser_config.get("base_url") or ""),
                browser_config.get("allowed_paths"),
            )
            title = await page.title()
            body_text = await page.locator("body").inner_text(timeout=timeout_ms)
            if loading_shell_present(body_text):
                raise BrowserScopeError("Browser page did not finish rendering within the configured read-only wait")
            elements = await page.locator("a, button, input, select, textarea").evaluate_all(
                """
                (nodes, limit) => nodes.slice(0, limit).map((node) => ({
                    tag: node.tagName.toLowerCase(),
                    text: (node.innerText || node.textContent || '').trim().slice(0, 240),
                    label: node.getAttribute('aria-label') || '',
                    type: node.getAttribute('type') || ''
                }))
                """,
                safe_element_limit,
            )
            return {
                "url": safe_page_url,
                "title": title[:500],
                "text": body_text[:safe_text_limit],
                "text_truncated": len(body_text) > safe_text_limit,
                "elements": elements,
            }
        finally:
            await context.close()
