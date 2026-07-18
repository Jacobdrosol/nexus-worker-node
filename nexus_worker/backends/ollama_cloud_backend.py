import json
import os
import re
from typing import Any, AsyncGenerator

import httpx
from fastapi import HTTPException


_THINK_BLOCK_PATTERN = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_THINK_TAG_PATTERN = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
_THINK_TAG_PREFIXES = ("<think>", "</think>")


def _clean_visible_output(value: Any) -> str:
    """Remove model reasoning markup that is not part of the worker result contract."""
    text = str(value or "")
    text = _THINK_BLOCK_PATTERN.sub("", text)
    return _THINK_TAG_PATTERN.sub("", text).strip()


class _VisibleOutputFilter:
    """Suppress reasoning tags while preserving normal streaming token delivery."""

    def __init__(self) -> None:
        self._buffer = ""
        self._inside_thinking = False

    def feed(self, value: Any) -> str:
        self._buffer += str(value or "")
        visible: list[str] = []

        while self._buffer:
            match = _THINK_TAG_PATTERN.search(self._buffer)
            if match:
                if not self._inside_thinking:
                    visible.append(self._buffer[:match.start()])
                self._inside_thinking = not match.group().lower().startswith("</")
                self._buffer = self._buffer[match.end():]
                continue

            suffix = self._possible_tag_suffix()
            current = self._buffer[:-len(suffix)] if suffix else self._buffer
            if not self._inside_thinking:
                visible.append(current)
            self._buffer = suffix
            break

        return "".join(visible)

    def finish(self) -> str:
        if self._inside_thinking:
            self._buffer = ""
            return ""
        value = _clean_visible_output(self._buffer)
        self._buffer = ""
        return value

    def _possible_tag_suffix(self) -> str:
        lower = self._buffer.lower()
        for size in range(min(len(lower), max(map(len, _THINK_TAG_PREFIXES)) - 1), 0, -1):
            suffix = lower[-size:]
            if any(prefix.startswith(suffix) for prefix in _THINK_TAG_PREFIXES):
                return self._buffer[-size:]
        return ""


def _ollama_options(params: dict) -> dict:
    options = dict(params or {})
    max_tokens = options.pop("max_tokens", None)
    if max_tokens is not None and "num_predict" not in options:
        options["num_predict"] = max_tokens
    return options


def _chat_body(model: str, messages: list[dict], params: dict, stream: bool) -> dict:
    request_params = dict(params or {})
    # Reasoning tokens can consume a small bounded response before a final answer is produced.
    # Keep worker responses deterministic unless a caller explicitly requests Ollama thinking.
    think = request_params.pop("think", False)
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "think": think,
        "options": _ollama_options(request_params),
    }


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
    body = _chat_body(model=model, messages=messages, params=params, stream=False)
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
    output = _clean_visible_output(message.get("content", "") if isinstance(message, dict) else "")
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
    body = _chat_body(model=model, messages=messages, params=params, stream=True)
    chunks: list[str] = []
    visible_output = _VisibleOutputFilter()
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
                        visible_text = visible_output.feed(text)
                        if visible_text:
                            yield {"event": "token", "text": visible_text}
                    if data.get("done"):
                        final_usage = {
                            "prompt_tokens": data.get("prompt_eval_count", 0),
                            "completion_tokens": data.get("eval_count", 0),
                        }
                        yield {
                            "event": "final",
                            "output": _clean_visible_output("".join(chunks)),
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
        trailing_text = visible_output.finish()
        if trailing_text:
            yield {"event": "token", "text": trailing_text}
        yield {
            "event": "final",
            "output": _clean_visible_output("".join(chunks)),
            "usage": final_usage,
        }
