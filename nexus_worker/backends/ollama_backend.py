import json
from typing import Any, AsyncGenerator

import httpx
from fastapi import HTTPException


def _ollama_options(params: dict) -> dict:
    options = dict(params or {})
    max_tokens = options.pop("max_tokens", None)
    if max_tokens is not None and "num_predict" not in options:
        options["num_predict"] = max_tokens
    return options


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
        "options": _ollama_options(params),
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


async def infer_stream(
    model: str,
    messages: list[dict],
    params: dict,
    host: str = "http://localhost:11434",
) -> AsyncGenerator[dict[str, Any], None]:
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": _ollama_options(params),
    }
    chunks: list[str] = []
    final_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", f"{host}/api/chat", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    text = str(data.get("message", {}).get("content", "") or "")
                    if text:
                        chunks.append(text)
                        yield {"event": "token", "text": text}
                    if data.get("done"):
                        final_usage = {
                            "prompt_tokens": data.get("prompt_eval_count", 0),
                            "completion_tokens": data.get("eval_count", 0),
                        }
        yield {
            "event": "final",
            "output": "".join(chunks),
            "usage": final_usage,
        }
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
