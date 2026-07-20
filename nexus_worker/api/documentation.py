"""Bounded documentation-hub worker endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from nexus_worker.documentation.hub import (
    DocumentationConflictError,
    DocumentationScopeError,
    write_documentation,
)
from nexus_worker.request_auth import require_worker_request_token


router = APIRouter(tags=["documentation"])


class DocumentationWriteRequest(BaseModel):
    """One explicitly bounded Docs Hub create or compare-and-save request."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: str = Field(min_length=4, max_length=6)
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=262_144)
    expected_content_hash: str | None = Field(default=None, validation_alias="expectedContentHash")


@router.post("/documentation/write")
async def documentation_write(request: Request, body: DocumentationWriteRequest) -> dict:
    cfg = getattr(request.app.state, "worker_config", {})
    require_worker_request_token(request, cfg)
    declared = getattr(request.app.state, "declared_worker_config", cfg)
    try:
        return await write_documentation(
            declared,
            action=body.action,
            path=body.path,
            content=body.content,
            expected_content_hash=body.expected_content_hash,
        )
    except DocumentationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DocumentationScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
