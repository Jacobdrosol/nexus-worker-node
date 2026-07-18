"""Read-only browser inspection endpoint for an attested browser worker."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nexus_worker.browser.inspector import BrowserScopeError, inspect_page

router = APIRouter(tags=["browser"])


class BrowserInspectRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    text_limit: int = Field(default=12000, ge=100, le=32000)
    element_limit: int = Field(default=40, ge=1, le=100)


def _require_browser_request_token(request: Request, browser_config: dict) -> None:
    token_env = str(browser_config.get("request_token_env") or "").strip()
    expected = os.environ.get(token_env, "").strip() if token_env else ""
    provided = request.headers.get("X-Nexus-Worker-Token", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Browser worker request token is not configured")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Browser worker request token is invalid")


@router.post("/browser/inspect")
async def browser_inspect(request: Request, body: BrowserInspectRequest) -> dict:
    browser_config = getattr(request.app.state, "browser_runtime_config", None)
    browser_attestation = getattr(request.app.state, "browser_attestation", {})
    if not isinstance(browser_config, dict) or not browser_attestation.get("ready"):
        raise HTTPException(status_code=503, detail="Read-only browser tooling is not available on this worker")
    _require_browser_request_token(request, browser_config)
    try:
        return await inspect_page(
            browser_config,
            path=body.path,
            text_limit=body.text_limit,
            element_limit=body.element_limit,
        )
    except BrowserScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
