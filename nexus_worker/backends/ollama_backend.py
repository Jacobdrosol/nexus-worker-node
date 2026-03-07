from typing import Any

import httpx
from fastapi import HTTPException


async def infer(
    model: str,
    messages: list[dict],
    params: dict,
    host: str = "http://localhost:11434",
) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": params,
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{host}/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
            output = data.get("message", {}).get("content", "")
            usage = {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            }
            return {"output": output, "usage": usage}
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Ollama request timed out for model {model} at {host}",
        ) from exc
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama host unreachable at {host}",
        ) from exc
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = ""
        try:
            if exc.response is not None:
                detail = (exc.response.text or "").strip()
        except Exception:
            detail = str(exc)
        raise HTTPException(
            status_code=status_code,
            detail=detail or f"Ollama request failed for model {model}",
        ) from exc
