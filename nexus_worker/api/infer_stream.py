import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from nexus_worker.services.inference import run_inference_stream

router = APIRouter(tags=["infer"])
logger = logging.getLogger(__name__)


class InferStreamRequest(BaseModel):
    model: str
    provider: str
    messages: List[Dict[str, Any]]
    params: Optional[Dict[str, Any]] = None
    command: Optional[str] = None


@router.post("/infer/stream")
async def infer_stream(request: Request, body: InferStreamRequest) -> StreamingResponse:
    cfg = getattr(request.app.state, "worker_config", {})

    async def event_gen() -> AsyncGenerator[str, None]:
        request.app.state.inference_inflight = int(
            getattr(request.app.state, "inference_inflight", 0) or 0
        ) + 1
        try:
            async for event in run_inference_stream(
                provider=body.provider,
                model=body.model,
                messages=body.messages,
                params=body.params or {},
                worker_config=cfg,
                command=body.command or "",
            ):
                event_name = str(event.get("event") or "message")
                payload = {k: v for k, v in event.items() if k != "event"}
                yield f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except HTTPException as e:
            payload = {"error": e.detail, "status_code": e.status_code}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        except Exception as e:
            logger.exception(
                "nexus_worker stream inference failed provider=%s model=%s",
                body.provider,
                body.model,
            )
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        finally:
            request.app.state.inference_inflight = max(
                0, int(getattr(request.app.state, "inference_inflight", 1)) - 1
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
