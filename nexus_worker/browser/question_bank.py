"""Constrained existing-question patches for GlobeIQ's Question Bank UI."""

from __future__ import annotations

import re
from typing import Any

from nexus_worker.browser.inspector import BrowserScopeError, scoped_url, validated_page_url


_ALLOWED_ACTIONS = {"patch_existing"}
_CREATE_ACTION = "create_one"
_EXPORT_ACTION = "export_evidence"
_READ_ONLY_EXPORT_ACTIONS = {"export json"}
_QUESTION_BANK_PATH = "/admin/question-bank/{bank_id}/questions"
_QUESTION_TYPES = {"MCQ", "TRUE_FALSE", "FREE_INPUT"}
_DIFFICULTIES = {"easy", "medium", "hard", "apply"}
_EXPECTED_FIELDS = {
    "prompt",
    "question_type",
    "difficulty",
    "category",
    "is_active",
    "options",
    "correct_option_index",
    "correct_answer",
}
_CHANGE_FIELDS = {
    "prompt",
    "difficulty",
    "category",
    "is_active",
    "options",
    "correct_option_index",
    "correct_answer",
}
_REVIEW_EVIDENCE_FIELDS = {
    "reviewer_bot_id",
    "review_task_id",
    "approved_patch",
    "semantic_duplicate_risk",
    "reviewed_question_ids",
    "shortage_detected",
    "rationale",
}
_CREATE_REVIEW_EVIDENCE_FIELDS = {
    "reviewer_bot_id",
    "review_task_id",
    "approved_create",
    "semantic_duplicate_risk",
    "reviewed_question_ids",
    "existing_question_count",
    "minimum_required_count",
    "shortage_detected",
    "rationale",
}
_CREATE_CANDIDATE_FIELDS = {
    "prompt",
    "question_type",
    "difficulty",
    "category",
    "is_active",
    "options",
    "correct_option_index",
    "correct_answer",
}
_SEMANTIC_DUPLICATE_RISKS = {"no_material_duplicate", "materially_distinct_context"}


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _required_text(value: Any, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise BrowserScopeError(f"Question Bank {label} must be text")
    normalized = _normalized_text(value)
    if not normalized or len(normalized) > maximum:
        raise BrowserScopeError(
            f"Question Bank {label} must be between one and {maximum} characters"
        )
    return normalized


def _optional_text(value: Any, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise BrowserScopeError(f"Question Bank {label} must be text")
    normalized = _normalized_text(value)
    if len(normalized) > maximum:
        raise BrowserScopeError(f"Question Bank {label} must be at most {maximum} characters")
    return normalized


def _validate_options(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not 3 <= len(value) <= 10:
        raise BrowserScopeError(f"Question Bank {label} must contain between three and ten options")
    return [_required_text(item, label=f"{label} option", maximum=2000) for item in value]


def _validate_correct_index(value: Any, *, option_count: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < option_count:
        raise BrowserScopeError(f"Question Bank {label} is outside the option range")
    return value


def _validate_review_evidence(
    browser_config: dict[str, Any], review_evidence: Any, *, question_id: int
) -> dict[str, Any]:
    if not isinstance(review_evidence, dict):
        raise BrowserScopeError("Question Bank patch requires reviewer evidence")
    unexpected_fields = sorted(set(review_evidence) - _REVIEW_EVIDENCE_FIELDS)
    if unexpected_fields:
        raise BrowserScopeError(
            "Question Bank reviewer evidence contains unsupported fields: "
            + ", ".join(unexpected_fields)
        )
    reviewer_bot_id = _required_text(
        review_evidence.get("reviewer_bot_id"), label="reviewer bot id", maximum=160
    )
    runtime = browser_config.get("question_bank_patch")
    configured_reviewer = str(runtime.get("reviewer_bot_id") or "").strip() if isinstance(runtime, dict) else ""
    if configured_reviewer and reviewer_bot_id != configured_reviewer:
        raise BrowserScopeError("Question Bank reviewer evidence came from an unauthorized reviewer")
    review_task_id = _required_text(
        review_evidence.get("review_task_id"), label="review task id", maximum=200
    )
    if review_evidence.get("approved_patch") is not True:
        raise BrowserScopeError("Question Bank patch requires reviewer approval")
    duplicate_risk = str(review_evidence.get("semantic_duplicate_risk") or "").strip()
    if duplicate_risk not in _SEMANTIC_DUPLICATE_RISKS:
        raise BrowserScopeError("Question Bank reviewer evidence does not clear semantic duplication risk")
    reviewed_ids = review_evidence.get("reviewed_question_ids")
    if (
        not isinstance(reviewed_ids, list)
        or not 1 <= len(reviewed_ids) <= 500
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in reviewed_ids)
        or question_id not in reviewed_ids
    ):
        raise BrowserScopeError("Question Bank reviewer evidence must include the target question id")
    if review_evidence.get("shortage_detected") is not False:
        raise BrowserScopeError("Question Bank patch action cannot add questions for a shortage")
    rationale = _required_text(review_evidence.get("rationale"), label="review rationale", maximum=2000)
    if len(rationale) < 32:
        raise BrowserScopeError("Question Bank reviewer rationale is too short")
    return {
        "reviewer_bot_id": reviewer_bot_id,
        "review_task_id": review_task_id,
        "semantic_duplicate_risk": duplicate_risk,
        "reviewed_question_ids": reviewed_ids,
        "rationale": rationale,
    }


def _validate_question_bank_create_review_evidence(
    browser_config: dict[str, Any], review_evidence: Any
) -> dict[str, Any]:
    if not isinstance(review_evidence, dict):
        raise BrowserScopeError("Question Bank creation requires reviewer evidence")
    unexpected_fields = sorted(set(review_evidence) - _CREATE_REVIEW_EVIDENCE_FIELDS)
    if unexpected_fields:
        raise BrowserScopeError(
            "Question Bank creation evidence contains unsupported fields: "
            + ", ".join(unexpected_fields)
        )
    reviewer_bot_id = _required_text(
        review_evidence.get("reviewer_bot_id"), label="reviewer bot id", maximum=160
    )
    runtime = browser_config.get("question_bank_patch")
    configured_reviewer = (
        str(runtime.get("reviewer_bot_id") or "").strip() if isinstance(runtime, dict) else ""
    )
    if configured_reviewer and reviewer_bot_id != configured_reviewer:
        raise BrowserScopeError("Question Bank creation evidence came from an unauthorized reviewer")
    review_task_id = _required_text(
        review_evidence.get("review_task_id"), label="review task id", maximum=200
    )
    if review_evidence.get("approved_create") is not True:
        raise BrowserScopeError("Question Bank creation requires reviewer approval")
    duplicate_risk = str(review_evidence.get("semantic_duplicate_risk") or "").strip()
    if duplicate_risk not in _SEMANTIC_DUPLICATE_RISKS:
        raise BrowserScopeError("Question Bank reviewer evidence does not clear semantic duplication risk")
    reviewed_ids = review_evidence.get("reviewed_question_ids")
    if (
        not isinstance(reviewed_ids, list)
        or len(reviewed_ids) > 500
        or len(set(reviewed_ids)) != len(reviewed_ids)
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in reviewed_ids)
    ):
        raise BrowserScopeError("Question Bank creation evidence must contain unique reviewed question ids")
    existing_question_count = review_evidence.get("existing_question_count")
    minimum_required_count = review_evidence.get("minimum_required_count")
    if (
        not isinstance(existing_question_count, int)
        or isinstance(existing_question_count, bool)
        or existing_question_count < 0
        or existing_question_count != len(reviewed_ids)
    ):
        raise BrowserScopeError("Question Bank creation evidence must cover the full existing bank")
    if (
        not isinstance(minimum_required_count, int)
        or isinstance(minimum_required_count, bool)
        or not existing_question_count < minimum_required_count <= 500
    ):
        raise BrowserScopeError("Question Bank creation requires a bounded verified shortage")
    if review_evidence.get("shortage_detected") is not True:
        raise BrowserScopeError("Question Bank creation requires a verified shortage")
    rationale = _required_text(review_evidence.get("rationale"), label="review rationale", maximum=2000)
    if len(rationale) < 32:
        raise BrowserScopeError("Question Bank reviewer rationale is too short")
    return {
        "reviewer_bot_id": reviewer_bot_id,
        "review_task_id": review_task_id,
        "semantic_duplicate_risk": duplicate_risk,
        "reviewed_question_ids": reviewed_ids,
        "existing_question_count": existing_question_count,
        "minimum_required_count": minimum_required_count,
        "rationale": rationale,
    }


def _validate_question_bank_create_candidate(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise BrowserScopeError("Question Bank creation requires a candidate question object")
    unexpected_fields = sorted(set(candidate) - _CREATE_CANDIDATE_FIELDS)
    if unexpected_fields:
        raise BrowserScopeError(
            "Question Bank candidate contains unsupported fields: " + ", ".join(unexpected_fields)
        )
    prompt = _required_text(candidate.get("prompt"), label="candidate prompt", maximum=4000)
    question_type = str(candidate.get("question_type") or "").strip().upper()
    if question_type not in _QUESTION_TYPES:
        raise BrowserScopeError("Question Bank candidate requires a supported question type")
    difficulty = str(candidate.get("difficulty") or "").strip().lower()
    if difficulty not in _DIFFICULTIES:
        raise BrowserScopeError("Question Bank candidate difficulty is invalid")
    category = _optional_text(candidate.get("category", ""), label="candidate category", maximum=160)
    is_active = candidate.get("is_active")
    if not isinstance(is_active, bool):
        raise BrowserScopeError("Question Bank candidate active value must be boolean")

    sanitized: dict[str, Any] = {
        "prompt": prompt,
        "question_type": question_type,
        "difficulty": difficulty,
        "category": category,
        "is_active": is_active,
    }
    if question_type == "MCQ":
        options = _validate_options(candidate.get("options"), label="candidate options")
        # The editor exposes a stable selector only for the three default inputs.
        # Keep automated creation at exactly three choices until the UI exposes a stable add-option hook.
        if len(options) != 3:
            raise BrowserScopeError("Question Bank automated MCQ creation requires exactly three options")
        sanitized["options"] = options
        sanitized["correct_option_index"] = _validate_correct_index(
            candidate.get("correct_option_index"),
            option_count=len(options),
            label="candidate correct option index",
        )
        if "correct_answer" in candidate:
            raise BrowserScopeError("Question Bank MCQ candidates cannot include a text answer")
    elif question_type == "TRUE_FALSE":
        answer = str(candidate.get("correct_answer") or "").strip().lower()
        if answer not in {"true", "false"}:
            raise BrowserScopeError("Question Bank True/False candidate answers must be true or false")
        if "options" in candidate or "correct_option_index" in candidate:
            raise BrowserScopeError("Question Bank True/False candidates cannot include options")
        sanitized["correct_answer"] = answer
    else:
        answer = _required_text(
            candidate.get("correct_answer"), label="candidate free-input answer", maximum=200
        )
        if len(answer.split()) != 1:
            raise BrowserScopeError("Question Bank free-input answers must be a single token")
        if "options" in candidate or "correct_option_index" in candidate:
            raise BrowserScopeError("Question Bank free-input candidates cannot include options")
        sanitized["correct_answer"] = answer
    return sanitized


def validate_question_bank_patch(
    browser_config: dict[str, Any],
    *,
    action: str,
    confirmation: str,
    bank_id: int,
    question_id: int,
    expected: dict[str, Any],
    changes: dict[str, Any],
    review_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Validate a single existing-question patch before a browser is launched."""

    normalized_action = str(action or "").strip().lower()
    if normalized_action not in _ALLOWED_ACTIONS:
        raise BrowserScopeError("Unsupported Question Bank action")
    if bank_id <= 0 or question_id <= 0:
        raise BrowserScopeError("Question Bank patches require positive bank and question ids")
    required_confirmation = f"approved:question-bank:{normalized_action}:{bank_id}:{question_id}"
    if str(confirmation or "").strip() != required_confirmation:
        raise BrowserScopeError("Question Bank patch requires exact single-question confirmation")
    runtime = browser_config.get("question_bank_patch")
    if not isinstance(runtime, dict) or not bool(runtime.get("enabled")):
        raise BrowserScopeError("Question Bank patches are not enabled on this worker")
    configured_actions = runtime.get("allowed_actions")
    if not isinstance(configured_actions, list) or normalized_action not in configured_actions:
        raise BrowserScopeError("Question Bank patch is not allowed on this worker")
    if not isinstance(expected, dict) or not isinstance(changes, dict):
        raise BrowserScopeError("Question Bank patch requires expected and changes objects")
    sanitized_review_evidence = _validate_review_evidence(
        browser_config, review_evidence, question_id=question_id
    )
    expected_unknown = sorted(set(expected) - _EXPECTED_FIELDS)
    change_unknown = sorted(set(changes) - _CHANGE_FIELDS)
    if expected_unknown or change_unknown:
        fields = expected_unknown + change_unknown
        raise BrowserScopeError("Question Bank patch contains unsupported fields: " + ", ".join(fields))
    if not changes:
        raise BrowserScopeError("Question Bank patch requires at least one change")

    prompt = _required_text(expected.get("prompt"), label="expected prompt", maximum=4000)
    question_type = str(expected.get("question_type") or "").strip().upper()
    if question_type not in _QUESTION_TYPES:
        raise BrowserScopeError("Question Bank patch requires a supported expected question type")

    sanitized_expected: dict[str, Any] = {"prompt": prompt, "question_type": question_type}
    if "difficulty" in expected:
        difficulty = str(expected["difficulty"] or "").strip().lower()
        if difficulty not in _DIFFICULTIES:
            raise BrowserScopeError("Question Bank expected difficulty is invalid")
        sanitized_expected["difficulty"] = difficulty
    if "category" in expected:
        sanitized_expected["category"] = _optional_text(
            expected["category"], label="expected category", maximum=160
        )
    if "is_active" in expected:
        if not isinstance(expected["is_active"], bool):
            raise BrowserScopeError("Question Bank expected active value must be boolean")
        sanitized_expected["is_active"] = expected["is_active"]
    if "options" in expected:
        if question_type != "MCQ":
            raise BrowserScopeError("Question Bank expected options are only valid for MCQ questions")
        sanitized_expected["options"] = _validate_options(
            expected["options"], label="expected options"
        )
    if "correct_option_index" in expected:
        if question_type != "MCQ":
            raise BrowserScopeError("Question Bank expected correct option is only valid for MCQ questions")
        option_count = len(sanitized_expected.get("options") or [])
        sanitized_expected["correct_option_index"] = _validate_correct_index(
            expected["correct_option_index"],
            option_count=option_count,
            label="expected correct option index",
        )
    if "correct_answer" in expected:
        if question_type != "TRUE_FALSE":
            raise BrowserScopeError(
                "Question Bank expected correct answer is only valid for True/False questions"
            )
        sanitized_expected["correct_answer"] = _required_text(
            expected["correct_answer"], label="expected correct answer", maximum=1000
        )

    sanitized_changes: dict[str, Any] = {}
    if "prompt" in changes:
        sanitized_changes["prompt"] = _required_text(
            changes["prompt"], label="replacement prompt", maximum=4000
        )
    if "difficulty" in changes:
        if "difficulty" not in sanitized_expected:
            raise BrowserScopeError("Question Bank difficulty patches require expected difficulty")
        difficulty = str(changes["difficulty"] or "").strip().lower()
        if difficulty not in _DIFFICULTIES:
            raise BrowserScopeError("Question Bank replacement difficulty is invalid")
        sanitized_changes["difficulty"] = difficulty
    if "category" in changes:
        if "category" not in sanitized_expected:
            raise BrowserScopeError("Question Bank category patches require expected category")
        sanitized_changes["category"] = _optional_text(
            changes["category"], label="replacement category", maximum=160
        )
    if "is_active" in changes:
        if "is_active" not in sanitized_expected or not isinstance(changes["is_active"], bool):
            raise BrowserScopeError("Question Bank active patches require expected boolean values")
        sanitized_changes["is_active"] = changes["is_active"]
    if "options" in changes:
        if question_type != "MCQ" or "options" not in sanitized_expected:
            raise BrowserScopeError("Question Bank option patches require expected MCQ options")
        options = _validate_options(changes["options"], label="replacement options")
        if len(options) != len(sanitized_expected["options"]):
            raise BrowserScopeError("Question Bank option patches cannot add or remove options")
        sanitized_changes["options"] = options
    if "correct_option_index" in changes:
        if question_type != "MCQ" or "correct_option_index" not in sanitized_expected:
            raise BrowserScopeError("Question Bank correct-option patches require expected MCQ state")
        option_count = len(sanitized_expected.get("options") or [])
        sanitized_changes["correct_option_index"] = _validate_correct_index(
            changes["correct_option_index"],
            option_count=option_count,
            label="replacement correct option index",
        )
    if "correct_answer" in changes:
        if question_type != "TRUE_FALSE" or "correct_answer" not in sanitized_expected:
            raise BrowserScopeError(
                "Question Bank correct-answer patches currently support True/False questions only"
            )
        answer = str(changes["correct_answer"] or "").strip().lower()
        if answer not in {"true", "false"}:
            raise BrowserScopeError("Question Bank True/False answers must be true or false")
        sanitized_changes["correct_answer"] = answer

    return {
        "action": normalized_action,
        "bank_id": bank_id,
        "question_id": question_id,
        "expected": sanitized_expected,
        "changes": sanitized_changes,
        "review_evidence": sanitized_review_evidence,
    }


def validate_question_bank_create(
    browser_config: dict[str, Any],
    *,
    action: str,
    confirmation: str,
    bank_id: int,
    candidate: dict[str, Any],
    review_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Validate a single shortage-driven Question Bank creation before browser launch."""

    normalized_action = str(action or "").strip().lower()
    if normalized_action != _CREATE_ACTION:
        raise BrowserScopeError("Unsupported Question Bank creation action")
    if bank_id <= 0:
        raise BrowserScopeError("Question Bank creation requires a positive bank id")
    runtime = browser_config.get("question_bank_patch")
    if not isinstance(runtime, dict) or not bool(runtime.get("enabled")):
        raise BrowserScopeError("Question Bank creation is not enabled on this worker")
    configured_actions = runtime.get("allowed_actions")
    if not isinstance(configured_actions, list) or normalized_action not in configured_actions:
        raise BrowserScopeError("Question Bank creation is not allowed on this worker")
    sanitized_review_evidence = _validate_question_bank_create_review_evidence(
        browser_config, review_evidence
    )
    required_confirmation = (
        f"approved:question-bank:{normalized_action}:{bank_id}:"
        f"{sanitized_review_evidence['review_task_id']}"
    )
    if str(confirmation or "").strip() != required_confirmation:
        raise BrowserScopeError("Question Bank creation requires exact one-question confirmation")
    return {
        "action": normalized_action,
        "bank_id": bank_id,
        "candidate": _validate_question_bank_create_candidate(candidate),
        "review_evidence": sanitized_review_evidence,
    }


def validate_question_bank_evidence_export(
    browser_config: dict[str, Any],
    *,
    action: str,
    bank_id: int,
    approved_read_only_actions: list[str],
) -> dict[str, Any]:
    """Validate one complete, read-only Question Bank evidence export."""

    normalized_action = str(action or "").strip().lower()
    if normalized_action != _EXPORT_ACTION:
        raise BrowserScopeError("Unsupported Question Bank evidence export action")
    if bank_id <= 0:
        raise BrowserScopeError("Question Bank evidence export requires a positive bank id")
    if (
        not isinstance(approved_read_only_actions, list)
        or set(approved_read_only_actions) != _READ_ONLY_EXPORT_ACTIONS
        or len(approved_read_only_actions) != 1
    ):
        raise BrowserScopeError("Question Bank evidence export requires approvedReadOnlyActions=[\"export json\"]")
    runtime = browser_config.get("question_bank_export")
    if not isinstance(runtime, dict) or not bool(runtime.get("enabled")):
        raise BrowserScopeError("Question Bank evidence export is not enabled on this worker")
    configured_actions = runtime.get("allowed_actions")
    if not isinstance(configured_actions, list) or normalized_action not in configured_actions:
        raise BrowserScopeError("Question Bank evidence export is not allowed on this worker")
    maximum_questions = runtime.get("maximum_questions", 500)
    if (
        not isinstance(maximum_questions, int)
        or isinstance(maximum_questions, bool)
        or not 1 <= maximum_questions <= 500
    ):
        raise BrowserScopeError("Question Bank evidence export maximum must be between one and 500")
    return {
        "action": normalized_action,
        "bank_id": bank_id,
        "maximum_questions": maximum_questions,
    }


async def _wait_for_search(page: Any, search: str, timeout_ms: int) -> None:
    await page.wait_for_function(
        """(expected) => document.querySelector('[data-testid="question-bank-result-state"]')
            ?.getAttribute('data-loaded-search') === expected""",
        search,
        timeout=timeout_ms,
    )


async def _set_search(page: Any, search: str, timeout_ms: int) -> None:
    field = page.get_by_test_id("question-bank-search")
    await field.wait_for(state="visible", timeout=timeout_ms)
    if await field.input_value() != search:
        await field.fill(search)
    await _wait_for_search(page, search, timeout_ms)


async def _editor_text(page: Any, test_id: str, timeout_ms: int) -> str:
    content = page.get_by_test_id(test_id).locator('[data-role="content"]')
    await content.wait_for(state="visible", timeout=timeout_ms)
    return _normalized_text(await content.inner_text())


async def _replace_editor_text(page: Any, test_id: str, value: str, timeout_ms: int) -> None:
    content = page.get_by_test_id(test_id).locator('[data-role="content"]')
    await content.wait_for(state="visible", timeout=timeout_ms)
    await content.fill(value)


async def _reject_exact_prompt_duplicate(
    page: Any,
    *,
    replacement_prompt: str,
    question_id: int | None,
    timeout_ms: int,
) -> None:
    await _set_search(page, replacement_prompt, timeout_ms)
    cards = page.get_by_test_id("question-bank-question-list").locator('[data-testid^="question-card-"]')
    for index in range(await cards.count()):
        card = cards.nth(index)
        card_id = await card.get_attribute("data-testid")
        if question_id is not None and card_id == f"question-card-{question_id}":
            continue
        match = re.fullmatch(r"question-card-(\d+)", card_id or "")
        if not match:
            continue
        prompt = await page.get_by_test_id(f"question-prompt-{match.group(1)}").inner_text()
        if _normalized_text(prompt) == replacement_prompt:
            raise BrowserScopeError("Question Bank patch would create an exact duplicate prompt")


async def execute_question_bank_patch(
    browser_config: dict[str, Any],
    *,
    action: str,
    confirmation: str,
    bank_id: int,
    question_id: int,
    expected: dict[str, Any],
    changes: dict[str, Any],
    review_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Patch one existing Question Bank question through the fixed Admin UI only."""

    request = validate_question_bank_patch(
        browser_config,
        action=action,
        confirmation=confirmation,
        bank_id=bank_id,
        question_id=question_id,
        expected=expected,
        changes=changes,
        review_evidence=review_evidence,
    )
    profile_dir = str(browser_config.get("user_data_dir") or "")
    if not profile_dir:
        raise BrowserScopeError("Question Bank patches require a persistent profile directory")
    timeout_seconds = browser_config.get("timeout_seconds")
    try:
        configured_timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        configured_timeout = 30
    # Existing private browser configs used milliseconds despite the historical
    # field name. New configs use seconds; support both without unbounded waits.
    timeout_ms = configured_timeout * 1000 if configured_timeout <= 1_200 else configured_timeout
    timeout_ms = max(1_000, min(timeout_ms, 600_000))
    target_url = scoped_url(
        str(browser_config.get("base_url") or ""),
        _QUESTION_BANK_PATH.format(bank_id=bank_id),
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
            expected_prompt = request["expected"]["prompt"]
            replacement_prompt = request["changes"].get("prompt")
            if replacement_prompt and replacement_prompt != expected_prompt:
                await _reject_exact_prompt_duplicate(
                    page,
                    replacement_prompt=replacement_prompt,
                    question_id=question_id,
                    timeout_ms=timeout_ms,
                )

            await _set_search(page, expected_prompt, timeout_ms)
            question_card = page.get_by_test_id(f"question-card-{question_id}")
            await question_card.wait_for(state="visible", timeout=timeout_ms)
            displayed_prompt = _normalized_text(
                await page.get_by_test_id(f"question-prompt-{question_id}").inner_text()
            )
            if displayed_prompt != expected_prompt:
                raise BrowserScopeError("Question Bank expected prompt no longer matches the target question")

            await page.get_by_test_id(f"question-edit-{question_id}").click()
            modal = page.get_by_test_id("question-editor-modal")
            await modal.wait_for(state="visible", timeout=timeout_ms)
            question_type = await page.get_by_test_id("question-editor-type").input_value()
            if question_type != request["expected"]["question_type"]:
                raise BrowserScopeError("Question Bank expected question type no longer matches")

            if "difficulty" in request["expected"]:
                current = await page.get_by_test_id("question-editor-difficulty").input_value()
                if current != request["expected"]["difficulty"]:
                    raise BrowserScopeError("Question Bank expected difficulty no longer matches")
            if "category" in request["expected"]:
                current = _normalized_text(
                    await page.get_by_test_id("question-editor-category").input_value()
                )
                if current != request["expected"]["category"]:
                    raise BrowserScopeError("Question Bank expected category no longer matches")
            if "is_active" in request["expected"]:
                current = await page.get_by_test_id("question-editor-active").is_checked()
                if current != request["expected"]["is_active"]:
                    raise BrowserScopeError("Question Bank expected active state no longer matches")
            if "options" in request["expected"]:
                for index, option in enumerate(request["expected"]["options"]):
                    current = await _editor_text(page, f"question-editor-option-{index}", timeout_ms)
                    if current != option:
                        raise BrowserScopeError("Question Bank expected option no longer matches")
            if "correct_option_index" in request["expected"]:
                correct_index = request["expected"]["correct_option_index"]
                if not await page.get_by_test_id(
                    f"question-editor-correct-option-{correct_index}"
                ).is_checked():
                    raise BrowserScopeError("Question Bank expected correct option no longer matches")
            if "correct_answer" in request["expected"]:
                current = await page.get_by_test_id("question-editor-true-false-answer").input_value()
                if current.lower() != request["expected"]["correct_answer"].lower():
                    raise BrowserScopeError("Question Bank expected correct answer no longer matches")

            if "prompt" in request["changes"]:
                await _replace_editor_text(
                    page, "question-editor-text-control", request["changes"]["prompt"], timeout_ms
                )
            if "difficulty" in request["changes"]:
                await page.get_by_test_id("question-editor-difficulty").select_option(
                    request["changes"]["difficulty"]
                )
            if "category" in request["changes"]:
                await page.get_by_test_id("question-editor-category").fill(
                    request["changes"]["category"]
                )
            if "is_active" in request["changes"]:
                active = page.get_by_test_id("question-editor-active")
                if await active.is_checked() != request["changes"]["is_active"]:
                    await active.click()
            if "options" in request["changes"]:
                for index, option in enumerate(request["changes"]["options"]):
                    await _replace_editor_text(page, f"question-editor-option-{index}", option, timeout_ms)
            if "correct_option_index" in request["changes"]:
                await page.get_by_test_id(
                    f"question-editor-correct-option-{request['changes']['correct_option_index']}"
                ).check()
            if "correct_answer" in request["changes"]:
                await page.get_by_test_id("question-editor-true-false-answer").select_option(
                    request["changes"]["correct_answer"]
                )

            await page.get_by_test_id("question-editor-save").click()
            try:
                await modal.wait_for(state="hidden", timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                raise BrowserScopeError("Question Bank patch did not save through the UI") from exc

            verification_prompt = request["changes"].get("prompt", expected_prompt)
            await _set_search(page, verification_prompt, timeout_ms)
            await question_card.wait_for(state="visible", timeout=timeout_ms)
            saved_prompt = _normalized_text(
                await page.get_by_test_id(f"question-prompt-{question_id}").inner_text()
            )
            if saved_prompt != verification_prompt:
                raise BrowserScopeError("Question Bank post-save prompt verification failed")
            return {
                "action": request["action"],
                "bank_id": bank_id,
                "question_id": question_id,
                "url": safe_page_url,
                "changed_fields": sorted(request["changes"]),
                "status": "Question Bank patch saved and verified",
            }
        finally:
            await context.close()


def _question_bank_total_count(page_text: str) -> int:
    match = re.search(r"Showing\s+\d+\s+of\s+(\d+)", page_text)
    if not match:
        raise BrowserScopeError("Question Bank UI did not expose a complete result count")
    return int(match.group(1))


async def execute_question_bank_evidence_export(
    browser_config: dict[str, Any],
    *,
    action: str,
    bank_id: int,
    approved_read_only_actions: list[str],
) -> dict[str, Any]:
    """Read one complete Question Bank from the Admin UI without changing it."""

    request = validate_question_bank_evidence_export(
        browser_config,
        action=action,
        bank_id=bank_id,
        approved_read_only_actions=approved_read_only_actions,
    )
    profile_dir = str(browser_config.get("user_data_dir") or "")
    if not profile_dir:
        raise BrowserScopeError("Question Bank evidence export requires a persistent profile directory")
    try:
        configured_timeout = int(browser_config.get("timeout_seconds"))
    except (TypeError, ValueError):
        configured_timeout = 30
    timeout_ms = configured_timeout * 1000 if configured_timeout <= 1_200 else configured_timeout
    timeout_ms = max(1_000, min(timeout_ms, 600_000))
    target_url = scoped_url(
        str(browser_config.get("base_url") or ""),
        _QUESTION_BANK_PATH.format(bank_id=bank_id),
        browser_config.get("allowed_paths"),
    )

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
            await _wait_for_search(page, "", timeout_ms)
            total_questions = _question_bank_total_count(await page.locator("body").inner_text())
            if total_questions > request["maximum_questions"]:
                raise BrowserScopeError(
                    "Question Bank evidence export exceeds the configured read-only question limit"
                )

            exported_questions: list[dict[str, Any]] = []
            seen_ids: set[int] = set()
            next_button = page.get_by_role("button", name="Next", exact=True)
            while True:
                cards = page.get_by_test_id("question-bank-question-list").locator(
                    '[data-testid^="question-card-"]'
                )
                card_count = await cards.count()
                if card_count == 0 and total_questions:
                    raise BrowserScopeError("Question Bank UI returned an empty page before the export completed")
                first_card_id: str | None = None
                for index in range(card_count):
                    card = cards.nth(index)
                    card_id = await card.get_attribute("data-testid")
                    match = re.fullmatch(r"question-card-(\d+)", card_id or "")
                    if not match:
                        continue
                    if first_card_id is None:
                        first_card_id = card_id
                    question_id = int(match.group(1))
                    if question_id in seen_ids:
                        raise BrowserScopeError("Question Bank UI repeated a question during the evidence export")
                    seen_ids.add(question_id)
                    prompt = _normalized_text(
                        await page.get_by_test_id(f"question-prompt-{question_id}").inner_text()
                    )
                    if not prompt:
                        raise BrowserScopeError("Question Bank UI returned an empty question prompt")
                    exported_questions.append(
                        {
                            "question_id": question_id,
                            "prompt": prompt,
                            "card_text": _normalized_text(await card.inner_text()),
                        }
                    )
                if len(exported_questions) == total_questions:
                    break
                if len(exported_questions) > total_questions or await next_button.is_disabled():
                    raise BrowserScopeError("Question Bank UI could not complete the evidence export")
                if not first_card_id:
                    raise BrowserScopeError("Question Bank UI did not expose a page cursor")
                await next_button.click()
                await page.wait_for_function(
                    """previous => document.querySelector('[data-testid^="question-card-"]')
                        ?.getAttribute('data-testid') !== previous""",
                    first_card_id,
                    timeout=timeout_ms,
                )
            return {
                "action": request["action"],
                "bank_id": bank_id,
                "question_count": total_questions,
                "questions": exported_questions,
                "complete": True,
                "url": safe_page_url,
                "status": "Question Bank evidence exported from the UI",
            }
        finally:
            await context.close()


async def execute_question_bank_create(
    browser_config: dict[str, Any],
    *,
    action: str,
    confirmation: str,
    bank_id: int,
    candidate: dict[str, Any],
    review_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Create exactly one shortage-approved Question Bank item through the Admin UI."""

    request = validate_question_bank_create(
        browser_config,
        action=action,
        confirmation=confirmation,
        bank_id=bank_id,
        candidate=candidate,
        review_evidence=review_evidence,
    )
    profile_dir = str(browser_config.get("user_data_dir") or "")
    if not profile_dir:
        raise BrowserScopeError("Question Bank creation requires a persistent profile directory")
    try:
        configured_timeout = int(browser_config.get("timeout_seconds"))
    except (TypeError, ValueError):
        configured_timeout = 30
    timeout_ms = configured_timeout * 1000 if configured_timeout <= 1_200 else configured_timeout
    timeout_ms = max(1_000, min(timeout_ms, 600_000))
    target_url = scoped_url(
        str(browser_config.get("base_url") or ""),
        _QUESTION_BANK_PATH.format(bank_id=bank_id),
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
            question = request["candidate"]
            await _reject_exact_prompt_duplicate(
                page,
                replacement_prompt=question["prompt"],
                question_id=None,
                timeout_ms=timeout_ms,
            )
            await page.get_by_test_id("question-bank-new-question").click()
            modal = page.get_by_test_id("question-editor-modal")
            await modal.wait_for(state="visible", timeout=timeout_ms)
            await page.get_by_test_id("question-editor-type").select_option(question["question_type"])
            await page.get_by_test_id("question-editor-difficulty").select_option(question["difficulty"])
            await _replace_editor_text(
                page, "question-editor-text-control", question["prompt"], timeout_ms
            )
            await page.get_by_test_id("question-editor-category").fill(question["category"])
            active = page.get_by_test_id("question-editor-active")
            if await active.is_checked() != question["is_active"]:
                await active.click()

            if question["question_type"] == "MCQ":
                for index, option in enumerate(question["options"]):
                    await _replace_editor_text(
                        page, f"question-editor-option-{index}", option, timeout_ms
                    )
                await page.get_by_test_id(
                    f"question-editor-correct-option-{question['correct_option_index']}"
                ).check()
            elif question["question_type"] == "TRUE_FALSE":
                answer = page.get_by_test_id("question-editor-true-false-answer")
                await answer.wait_for(state="visible", timeout=timeout_ms)
                await answer.select_option(question["correct_answer"])
            else:
                answer = page.get_by_test_id("question-editor-free-input-answer")
                await answer.wait_for(state="visible", timeout=timeout_ms)
                await answer.fill(question["correct_answer"])

            await page.get_by_test_id("question-editor-save").click()
            try:
                await modal.wait_for(state="hidden", timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                raise BrowserScopeError("Question Bank creation did not save through the UI") from exc

            await _set_search(page, question["prompt"], timeout_ms)
            cards = page.get_by_test_id("question-bank-question-list").locator(
                '[data-testid^="question-card-"]'
            )
            exact_matches: list[int] = []
            for index in range(await cards.count()):
                card = cards.nth(index)
                card_id = await card.get_attribute("data-testid")
                match = re.fullmatch(r"question-card-(\d+)", card_id or "")
                if not match:
                    continue
                saved_prompt = _normalized_text(
                    await page.get_by_test_id(f"question-prompt-{match.group(1)}").inner_text()
                )
                if saved_prompt == question["prompt"]:
                    exact_matches.append(int(match.group(1)))
            if len(exact_matches) != 1:
                raise BrowserScopeError("Question Bank post-save verification found an ambiguous prompt")
            return {
                "action": request["action"],
                "bank_id": bank_id,
                "question_id": exact_matches[0],
                "url": safe_page_url,
                "created_fields": sorted(question),
                "review_task_id": request["review_evidence"]["review_task_id"],
                "status": "Question Bank question created and verified",
            }
        finally:
            await context.close()
