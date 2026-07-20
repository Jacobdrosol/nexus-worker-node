"""Allowlisted access to a GlobeIQ-compatible documentation hub.

This module intentionally exposes only Markdown create and compare-and-save
operations.  It cannot browse, rename, delete, upload, or access arbitrary
application endpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_DEFAULT_MAX_CONTENT_BYTES = 65_536
_SESSION_COOKIES: dict[str, dict[str, str]] = {}
_SESSION_LOCK = asyncio.Lock()


class DocumentationScopeError(ValueError):
    """Raised when a documentation request falls outside the worker contract."""


class DocumentationConflictError(DocumentationScopeError):
    """Raised when an existing document changed since its expected hash."""


def _required_path(value: Any, name: str) -> str:
    path = str(value or "").strip()
    if not path.startswith("/") or "?" in path or "#" in path or "//" in path:
        raise DocumentationScopeError(f"documentation hub {name} is invalid")
    return path


def _normalized_root(value: Any) -> str:
    root = str(value or "").strip().replace("\\", "/").strip("/")
    if not root or root.startswith("../") or "/../" in root or root == "..":
        raise DocumentationScopeError("documentation hub allowed root is invalid")
    return root


def documentation_runtime_config(worker_config: dict[str, Any]) -> dict[str, Any] | None:
    """Return normalized private hub config only when it is explicitly enabled."""

    tooling = worker_config.get("tooling")
    raw = tooling.get("documentation_hub") if isinstance(tooling, dict) else None
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None

    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise DocumentationScopeError("documentation hub base_url must be an HTTPS origin")

    allowed_roots = raw.get("allowed_roots")
    if not isinstance(allowed_roots, list) or not 1 <= len(allowed_roots) <= 16:
        raise DocumentationScopeError("documentation hub requires one to sixteen allowed roots")
    normalized_roots = sorted({_normalized_root(item) for item in allowed_roots})

    username_env = str(raw.get("username_env") or "").strip()
    password_env = str(raw.get("password_env") or "").strip()
    if not _ENV_NAME_RE.fullmatch(username_env) or not _ENV_NAME_RE.fullmatch(password_env):
        raise DocumentationScopeError("documentation hub credential environment names are invalid")

    try:
        max_content_bytes = int(raw.get("max_content_bytes", _DEFAULT_MAX_CONTENT_BYTES))
        timeout_seconds = float(raw.get("timeout_seconds", 20))
    except (TypeError, ValueError) as exc:
        raise DocumentationScopeError("documentation hub numeric limits are invalid") from exc
    if not 1_024 <= max_content_bytes <= 262_144:
        raise DocumentationScopeError("documentation hub max_content_bytes must be between 1024 and 262144")
    if not 2 <= timeout_seconds <= 60:
        raise DocumentationScopeError("documentation hub timeout_seconds must be between 2 and 60")

    return {
        "base_url": base_url,
        "allowed_roots": normalized_roots,
        "username_env": username_env,
        "password_env": password_env,
        "max_content_bytes": max_content_bytes,
        "timeout_seconds": timeout_seconds,
        "login_path": _required_path(raw.get("login_path", "/api/admin/auth/login"), "login_path"),
        "content_path": _required_path(
            raw.get("content_path", "/api/admin/documentation/content"), "content_path"
        ),
        "create_path": _required_path(
            raw.get("create_path", "/api/admin/documentation/create"), "create_path"
        ),
        "save_path": _required_path(raw.get("save_path", "/api/admin/documentation/save"), "save_path"),
    }


def attest_documentation_runtime(worker_config: dict[str, Any]) -> dict[str, Any]:
    """Report whether this node can truthfully advertise the docs tool.

    This validates configuration and credential *presence* only.  It deliberately
    does not call the remote application during startup or expose any secret.
    """

    try:
        config = documentation_runtime_config(worker_config)
    except (DocumentationScopeError, TypeError, ValueError) as exc:
        return {"configured": True, "ready": False, "reason": str(exc)}
    if config is None:
        return {"configured": False, "ready": False}
    if not os.environ.get(config["username_env"], "").strip() or not os.environ.get(
        config["password_env"], ""
    ).strip():
        return {"configured": True, "ready": False, "reason": "documentation_hub_credentials_unavailable"}
    return {
        "configured": True,
        "ready": True,
        "provider": "documentation",
        "model": "documentation-v1",
        "allowed_roots": list(config["allowed_roots"]),
    }


def validate_documentation_path(config: dict[str, Any], path: str) -> str:
    """Normalize one Markdown file path and enforce the configured root allowlist."""

    normalized = str(path or "").strip().replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized or normalized == "..":
        raise DocumentationScopeError("documentation path is invalid")
    if "/./" in normalized or normalized.startswith("./") or normalized.endswith("/"):
        raise DocumentationScopeError("documentation path is invalid")
    if not normalized.casefold().endswith(".md"):
        raise DocumentationScopeError("documentation workers may write Markdown files only")
    if not any(normalized == root or normalized.startswith(f"{root}/") for root in config["allowed_roots"]):
        raise DocumentationScopeError("documentation path is outside this worker's allowed roots")
    return normalized


def _validated_content(config: dict[str, Any], content: str) -> str:
    if not isinstance(content, str):
        raise DocumentationScopeError("documentation content must be text")
    if len(content.encode("utf-8")) > int(config["max_content_bytes"]):
        raise DocumentationScopeError("documentation content exceeds this worker's size limit")
    return content


def _response_error(response: httpx.Response, operation: str) -> DocumentationScopeError:
    if response.status_code in {401, 403}:
        return DocumentationScopeError(f"documentation hub rejected {operation} authorization")
    if response.status_code == 404:
        return DocumentationScopeError(f"documentation hub {operation} target was not found")
    if response.status_code == 409:
        return DocumentationConflictError(f"documentation hub {operation} conflicted with existing state")
    return DocumentationScopeError(f"documentation hub {operation} failed with status {response.status_code}")


def _session_cache_key(config: dict[str, Any], username: str) -> str:
    return "\x00".join((config["base_url"], config["login_path"], username))


async def _session_cookies(
    config: dict[str, Any],
    *,
    username: str,
    password: str,
    timeout: httpx.Timeout,
) -> dict[str, str]:
    """Authenticate once per worker process and reuse the issued session cookie."""

    cache_key = _session_cache_key(config, username)
    cached = _SESSION_COOKIES.get(cache_key)
    if cached:
        return dict(cached)

    async with _SESSION_LOCK:
        cached = _SESSION_COOKIES.get(cache_key)
        if cached:
            return dict(cached)

        async with httpx.AsyncClient(
            base_url=config["base_url"], timeout=timeout, follow_redirects=False
        ) as client:
            login = await client.post(
                config["login_path"], json={"username": username, "password": password}
            )
            if not login.is_success:
                raise _response_error(login, "login")
            cookies = dict(client.cookies)

        if not cookies:
            raise DocumentationScopeError("documentation hub login did not establish a session")
        _SESSION_COOKIES[cache_key] = cookies
        return dict(cookies)


async def write_documentation(
    worker_config: dict[str, Any],
    *,
    action: str,
    path: str,
    content: str,
    expected_content_hash: str | None = None,
) -> dict[str, Any]:
    """Create one new document or compare-and-save one existing document."""

    config = documentation_runtime_config(worker_config)
    if config is None:
        raise DocumentationScopeError("documentation tooling is not enabled on this worker")
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"create", "save"}:
        raise DocumentationScopeError("documentation action must be create or save")
    normalized_path = validate_documentation_path(config, path)
    normalized_content = _validated_content(config, content)
    if normalized_action == "save":
        normalized_hash = str(expected_content_hash or "").strip().lower()
        if not _HASH_RE.fullmatch(normalized_hash):
            raise DocumentationScopeError("documentation save requires an expected SHA-256 content hash")
    elif expected_content_hash is not None:
        raise DocumentationScopeError("documentation create does not accept an expected content hash")

    username = os.environ.get(config["username_env"], "").strip()
    password = os.environ.get(config["password_env"], "").strip()
    if not username or not password:
        raise DocumentationScopeError("documentation hub credentials are unavailable")

    timeout = httpx.Timeout(float(config["timeout_seconds"]))
    try:
        cookies = await _session_cookies(
            config, username=username, password=password, timeout=timeout
        )
        async with httpx.AsyncClient(
            base_url=config["base_url"], timeout=timeout, follow_redirects=False, cookies=cookies
        ) as client:

            if normalized_action == "save":
                current = await client.get(config["content_path"], params={"path": normalized_path})
                if not current.is_success:
                    raise _response_error(current, "content lookup")
                response_data = current.json()
                current_hash = str(response_data.get("contentHash") or "").strip().lower() if isinstance(response_data, dict) else ""
                if not _HASH_RE.fullmatch(current_hash) or not hmac.compare_digest(current_hash, normalized_hash):
                    raise DocumentationConflictError("documentation content changed before this save could be applied")
                write = await client.post(
                    config["save_path"], json={"path": normalized_path, "content": normalized_content}
                )
            else:
                write = await client.post(
                    config["create_path"],
                    json={"path": normalized_path, "isDirectory": False, "content": normalized_content},
                )
            if not write.is_success:
                raise _response_error(write, f"{normalized_action} write")
    except httpx.HTTPError as exc:
        raise DocumentationScopeError("documentation hub request failed") from exc

    return {
        "action": normalized_action,
        "path": normalized_path,
        "content_hash": hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
        "bytes_written": len(normalized_content.encode("utf-8")),
    }
