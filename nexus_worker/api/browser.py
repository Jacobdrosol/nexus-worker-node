"""Bounded browser endpoints for an attested browser worker."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from nexus_worker.browser.inspector import BrowserScopeError, inspect_page
from nexus_worker.browser.lesson_export import export_lesson_builder_json
from nexus_worker.browser.question_bank import (
    execute_question_bank_create,
    execute_question_bank_evidence_export,
    execute_question_bank_patch,
)
from nexus_worker.browser.test_builder import execute_test_builder_action

router = APIRouter(tags=["browser"])


class BrowserInspectRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    text_limit: int = Field(default=12000, ge=100, le=32000)
    element_limit: int = Field(default=40, ge=1, le=100)


class BrowserLessonExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    course_id: int = Field(ge=1)
    lesson_id: int = Field(ge=1)
    approved_read_only_actions: list[str] = Field(
        min_length=1,
        max_length=4,
        validation_alias="approvedReadOnlyActions",
    )


class TestBuilderBankSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    easy: int = Field(default=0, ge=0, le=100)
    medium: int = Field(default=0, ge=0, le=100)
    hard: int = Field(default=0, ge=0, le=100)
    apply: int = Field(default=0, ge=0, le=100)

class TestBuilderActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class QuestionBankExpectedState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)
    question_type: str = Field(min_length=1, max_length=40)
    difficulty: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=160)
    is_active: bool | None = None
    options: list[str] | None = Field(default=None, min_length=3, max_length=10)
    correct_option_index: int | None = Field(default=None, ge=0, le=9)
    correct_answer: str | None = Field(default=None, max_length=1000)


class QuestionBankPatchChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    difficulty: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=160)
    is_active: bool | None = None
    options: list[str] | None = Field(default=None, min_length=3, max_length=10)
    correct_option_index: int | None = Field(default=None, ge=0, le=9)
    correct_answer: str | None = Field(default=None, max_length=1000)


class QuestionBankReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_bot_id: str = Field(min_length=1, max_length=160)
    review_task_id: str = Field(min_length=1, max_length=200)
    approved_patch: bool
    semantic_duplicate_risk: str = Field(min_length=1, max_length=80)
    reviewed_question_ids: list[int] = Field(min_length=1, max_length=500)
    shortage_detected: bool
    rationale: str = Field(min_length=32, max_length=2000)


class QuestionBankPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=80)
    confirmation: str = Field(min_length=1, max_length=160)
    bank_id: int = Field(ge=1)
    question_id: int = Field(ge=1)
    expected: QuestionBankExpectedState
    changes: QuestionBankPatchChanges
    review_evidence: QuestionBankReviewEvidence


class QuestionBankCreateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)
    question_type: str = Field(min_length=1, max_length=40)
    difficulty: str = Field(min_length=1, max_length=40)
    category: str = Field(default="", max_length=160)
    is_active: bool
    options: list[str] | None = Field(default=None, min_length=3, max_length=10)
    correct_option_index: int | None = Field(default=None, ge=0, le=9)
    correct_answer: str | None = Field(default=None, max_length=1000)


class QuestionBankCreateReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_bot_id: str = Field(min_length=1, max_length=160)
    review_task_id: str = Field(min_length=1, max_length=200)
    approved_create: bool
    semantic_duplicate_risk: str = Field(min_length=1, max_length=80)
    reviewed_question_ids: list[int] = Field(max_length=500)
    existing_question_count: int = Field(ge=0, le=500)
    minimum_required_count: int = Field(ge=1, le=500)
    shortage_detected: bool
    rationale: str = Field(min_length=32, max_length=2000)


class QuestionBankCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=80)
    confirmation: str = Field(min_length=1, max_length=480)
    bank_id: int = Field(ge=1)
    candidate: QuestionBankCreateCandidate
    review_evidence: QuestionBankCreateReviewEvidence


class QuestionBankEvidenceExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: str = Field(min_length=1, max_length=80)
    bank_id: int = Field(ge=1)
    approved_read_only_actions: list[str] = Field(
        min_length=1,
        max_length=4,
        validation_alias="approvedReadOnlyActions",
    )

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


@router.post("/browser/lesson-export")
async def browser_lesson_export(request: Request, body: BrowserLessonExportRequest) -> dict:
    browser_config = getattr(request.app.state, "browser_runtime_config", None)
    browser_attestation = getattr(request.app.state, "browser_attestation", {})
    if not isinstance(browser_config, dict) or not browser_attestation.get("ready"):
        raise HTTPException(status_code=503, detail="Read-only browser tooling is not available on this worker")
    _require_browser_request_token(request, browser_config)
    try:
        return await export_lesson_builder_json(
            browser_config,
            course_id=body.course_id,
            lesson_id=body.lesson_id,
            approved_read_only_actions=body.approved_read_only_actions,
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
            banks=[selection.model_dump() for selection in body.banks],
            acknowledge_attempt_reset=body.acknowledge_attempt_reset,
        )
    except BrowserScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/browser/question-bank")
async def browser_question_bank_patch(request: Request, body: QuestionBankPatchRequest) -> dict:
    browser_config = getattr(request.app.state, "browser_runtime_config", None)
    browser_attestation = getattr(request.app.state, "browser_attestation", {})
    if not isinstance(browser_config, dict) or not browser_attestation.get("ready"):
        raise HTTPException(status_code=503, detail="Browser tooling is not available on this worker")
    _require_browser_request_token(request, browser_config)
    try:
        return await execute_question_bank_patch(
            browser_config,
            action=body.action,
            confirmation=body.confirmation,
            bank_id=body.bank_id,
            question_id=body.question_id,
            expected=body.expected.model_dump(exclude_none=True),
            changes=body.changes.model_dump(exclude_none=True),
            review_evidence=body.review_evidence.model_dump(),
        )
    except BrowserScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/browser/question-bank-create")
async def browser_question_bank_create(request: Request, body: QuestionBankCreateRequest) -> dict:
    browser_config = getattr(request.app.state, "browser_runtime_config", None)
    browser_attestation = getattr(request.app.state, "browser_attestation", {})
    if not isinstance(browser_config, dict) or not browser_attestation.get("ready"):
        raise HTTPException(status_code=503, detail="Browser tooling is not available on this worker")
    _require_browser_request_token(request, browser_config)
    try:
        return await execute_question_bank_create(
            browser_config,
            action=body.action,
            confirmation=body.confirmation,
            bank_id=body.bank_id,
            candidate=body.candidate.model_dump(exclude_none=True),
            review_evidence=body.review_evidence.model_dump(),
        )
    except BrowserScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/browser/question-bank-export")
async def browser_question_bank_evidence_export(
    request: Request, body: QuestionBankEvidenceExportRequest
) -> dict:
    browser_config = getattr(request.app.state, "browser_runtime_config", None)
    browser_attestation = getattr(request.app.state, "browser_attestation", {})
    if not isinstance(browser_config, dict) or not browser_attestation.get("ready"):
        raise HTTPException(status_code=503, detail="Read-only browser tooling is not available on this worker")
    _require_browser_request_token(request, browser_config)
    try:
        return await execute_question_bank_evidence_export(
            browser_config,
            action=body.action,
            bank_id=body.bank_id,
            approved_read_only_actions=body.approved_read_only_actions,
        )
    except BrowserScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
