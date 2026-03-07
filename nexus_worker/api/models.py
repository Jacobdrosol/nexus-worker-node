from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from nexus_worker.manager.local_models import discover_local_models

router = APIRouter(tags=["models"])


class PullModelRequest(BaseModel):
    model: str
    provider: Optional[str] = "ollama"


@router.get("/models/local")
async def local_models(request: Request) -> dict:
    cfg = getattr(request.app.state, "worker_config", {})
    return await discover_local_models(cfg)


@router.post("/models/local/pull")
async def pull_local_model(request: Request, body: PullModelRequest) -> dict:
    cfg = getattr(request.app.state, "worker_config", {})
    model = (body.model or "").strip()
    provider = (body.provider or "ollama").strip().lower()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    if provider != "ollama":
        raise HTTPException(status_code=400, detail=f"Unsupported local model provider: {provider}")

    host = str(cfg.get("ollama_host") or "http://localhost:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                f"{host}/api/pull",
                json={"model": model, "stream": False},
            )
            response.raise_for_status()
            data = response.json() if response.text else {}
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"Ollama pull timed out for model {model} at {host}") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama host unreachable at {host}") from exc
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = ""
        try:
            if exc.response is not None:
                detail = (exc.response.text or "").strip()
        except Exception:
            detail = str(exc)
        raise HTTPException(status_code=status_code, detail=detail or f"Ollama pull failed for model {model}") from exc

    return {
        "provider": provider,
        "model": model,
        "status": data.get("status") or "ok",
        "detail": data,
    }
