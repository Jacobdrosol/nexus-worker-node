"""Bounded browser endpoints for an attested browser worker."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nexus_worker.browser.inspector import BrowserScopeError, inspect_page
from nexus_worker.browser.test_builder import execute_test_builder_action

router = APIRouter(tags=["browser"])


class BrowserInspectRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    text_limit: int = Field(default=12000, ge=100, le=32000)
    element_limit: int = Field(default=40, ge=1, le=100)


class TestBuilderBankSelection(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    easy: int = Field(default=0, ge=0, le=100)
    medium: int = Field(default=0, ge=0, le=100)
    hard: int = Field(default=0, ge=0, le=100)
    apply: int = Field(default=0, ge=0, le=100)

    class Config:
        extra = "forbid"


class TestBuilderActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=80)
    mode: str = Field(default="draft", min_length=1, max_length=40)
    confirmation: str = Field(min_length=1, max_length=120)
    course_id: int = Field(ge=1)
    lesson_id: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=240)
    pass_threshold_pct: int = Field(ge=0, le=100)
    time_limit_seconds: int | None = Field(default=None, ge=0, le=86_400)
    allow_review: bool
    banks: list[TestBuilderBankSelection] = Field(min_length=1, max_length=12)
    acknowledge_attempt_reset: bool = False

    class Config:
        extra = "forbid"


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


@router.post("/browser/test-builder")
async def browser_test_builder(request: Request, body: TestBuilderActionRequest) -> dict:
    browser_config = getattr(request.app.state, "browser_runtime_config", None)
    browser_attestation = getattr(request.app.state, "browser_attestation", {})
    if not isinstance(browser_config, dict) or not browser_attestation.get("ready"):
        raise HTTPException(status_code=503, detail="Browser tooling is not available on this worker")
    _require_browser_request_token(request, browser_config)
    try:
        return await execute_test_builder_action(
            browser_config,
            action=body.action,
            mode=body.mode,
            confirmation=body.confirmation,
            course_id=body.course_id,
            lesson_id=body.lesson_id,
            title=body.title,
            pass_threshold_pct=body.pass_threshold_pct,
            time_limit_seconds=body.time_limit_seconds,
            allow_review=body.allow_review,
            banks=[selection.dict() for selection in body.banks],
            acknowledge_attempt_reset=body.acknowledge_attempt_reset,
        )
    except BrowserScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
