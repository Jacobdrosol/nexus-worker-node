import json
import os
from typing import Any, AsyncGenerator

import httpx
from fastapi import HTTPException


def _ollama_options(params: dict) -> dict:
    options = dict(params or {})
    max_tokens = options.pop("max_tokens", None)
    if max_tokens is not None and "num_predict" not in options:
        options["num_predict"] = max_tokens
    return options


def _cloud_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=10.0, read=None, write=120.0, pool=30.0)


def _base_url() -> str:
    return os.environ.get("OLLAMA_CLOUD_BASE_URL", "https://ollama.com/api").rstrip("/")


def _api_key() -> str:
    return os.environ.get("OLLAMA_API_KEY", "").strip()


def _headers() -> dict[str, str]:
    api_key = _api_key()
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OLLAMA_API_KEY is not configured on this worker",
        )
    return {"Authorization": f"Bearer {api_key}"}


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(
                data.get("error")
                or data.get("detail")
                or data.get("message")
                or ""
            ).strip()
    except Exception:
        pass
    return (response.text or "").strip()


async def infer(
    model: str,
    messages: list[dict],
    params: dict,
) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": _ollama_options(params),
    }
    try:
        async with httpx.AsyncClient(timeout=_cloud_timeout()) as client:
            response = await client.post(
                f"{_base_url()}/chat",
                headers=_headers(),
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Ollama Cloud request timed out for model {model}",
        ) from exc
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama Cloud host unreachable at {_base_url()}",
        ) from exc
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = _error_detail(exc.response) if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=status_code,
            detail=detail or f"Ollama Cloud request failed for model {model}",
        ) from exc

    message = data.get("message") if isinstance(data, dict) else {}
    output = message.get("content", "") if isinstance(message, dict) else ""
    usage = {
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
    }
    result: dict[str, Any] = {"output": output, "usage": usage}
    finish_reason = str(data.get("done_reason") or data.get("finish_reason") or "").strip()
    if finish_reason:
        result["finish_reason"] = finish_reason
    return result


async def infer_stream(
    model: str,
    messages: list[dict],
    params: dict,
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
        async with httpx.AsyncClient(timeout=_cloud_timeout()) as client:
            async with client.stream(
                "POST",
                f"{_base_url()}/chat",
                headers=_headers(),
                json=body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    message = data.get("message") if isinstance(data, dict) else {}
                    text = str(message.get("content", "") if isinstance(message, dict) else "")
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
                        return
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Ollama Cloud request timed out for model {model}",
        ) from exc
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama Cloud host unreachable at {_base_url()}",
        ) from exc
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = _error_detail(exc.response) if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=status_code,
            detail=detail or f"Ollama Cloud request failed for model {model}",
        ) from exc

    if chunks:
        yield {
            "event": "final",
            "output": "".join(chunks),
            "usage": final_usage,
        }
