"""Private Anthropic Messages compatibility gateway backed by Ollama Cloud.

This gateway exists for isolated CLI runtimes such as Claude Code.  It exposes
only the Messages endpoint on a private container network and translates the
limited request/response surface needed for tool-capable coding workflows to
Ollama Cloud's documented chat API.  It intentionally does not proxy arbitrary
URLs, expose credentials, or allow callers to select a model at request time.
"""

from __future__ import annotations

import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

DEFAULT_CLOUD_URL = "https://ollama.com/api"
DEFAULT_MODEL = "glm-5.2:cloud"
MAX_BODY_BYTES = 2_000_000
MAX_TOOLS = 64


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _gateway_token() -> str:
    return os.environ.get("NEXUS_OLLAMA_CLAUDE_GATEWAY_TOKEN", "").strip()


def _api_key() -> str:
    return os.environ.get("OLLAMA_API_KEY", "").strip()


def _model() -> str:
    return os.environ.get("NEXUS_OLLAMA_CLAUDE_GATEWAY_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _cloud_url() -> str:
    return os.environ.get("OLLAMA_CLOUD_BASE_URL", DEFAULT_CLOUD_URL).rstrip("/")


def _max_body_bytes() -> int:
    return _env_int(
        "NEXUS_OLLAMA_CLAUDE_GATEWAY_MAX_BODY_BYTES",
        MAX_BODY_BYTES,
        minimum=1_024,
        maximum=10_000_000,
    )


def _require_authentication(request: Request) -> None:
    expected = _gateway_token()
    if not expected:
        raise HTTPException(status_code=503, detail="Claude gateway token is not configured")
    provided = request.headers.get("x-api-key", "").strip()
    if not provided:
        authorization = request.headers.get("authorization", "").strip()
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Claude gateway token is invalid")


async def _read_json_body(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            if int(content_length) > _max_body_bytes():
                raise HTTPException(status_code=413, detail="Claude gateway request is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Claude gateway content length is invalid") from None
    raw = await request.body()
    if len(raw) > _max_body_bytes():
        raise HTTPException(status_code=413, detail="Claude gateway request is too large")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Claude gateway request must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Claude gateway request must be an object")
    return payload


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_as_text(item) for item in value if _as_text(item))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), str):
            return value["content"]
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value or "")


def _content_blocks(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _system_message(value: Any) -> str:
    if isinstance(value, str):
        return value
    return "\n".join(
        str(block.get("text") or "")
        for block in _content_blocks(value)
        if str(block.get("type") or "").lower() == "text"
    ).strip()


def _anthropic_tools_to_ollama(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="Claude gateway tools must be a list")
    if len(value) > MAX_TOOLS:
        raise HTTPException(status_code=400, detail="Claude gateway tool count exceeds the limit")
    tools: list[dict[str, Any]] = []
    for tool in value:
        if not isinstance(tool, dict):
            raise HTTPException(status_code=400, detail="Claude gateway tool must be an object")
        name = str(tool.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Claude gateway tool name is required")
        parameters = tool.get("input_schema")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description") or ""),
                    "parameters": parameters,
                },
            }
        )
    return tools


def _anthropic_messages_to_ollama(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise HTTPException(status_code=400, detail="Claude gateway messages are required")

    messages: list[dict[str, Any]] = []
    system = _system_message(payload.get("system"))
    if system:
        messages.append({"role": "system", "content": system})

    tool_names: dict[str, str] = {}
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            raise HTTPException(status_code=400, detail="Claude gateway message must be an object")
        role = str(raw_message.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system", "developer"}:
            raise HTTPException(status_code=400, detail=f"Claude gateway message role is unsupported: {role or 'missing'}")
        blocks = _content_blocks(raw_message.get("content"))
        if role in {"system", "developer"}:
            system_content = "\n".join(
                str(block.get("text") or "")
                for block in blocks
                if str(block.get("type") or "text").strip().lower() == "text"
            ).strip()
            if system_content:
                messages.append({"role": "system", "content": system_content})
            continue
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for block in blocks:
            block_type = str(block.get("type") or "text").strip().lower()
            if block_type == "text":
                text = str(block.get("text") or "")
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use" and role == "assistant":
                name = str(block.get("name") or "").strip()
                call_id = str(block.get("id") or "").strip()
                arguments = block.get("input")
                if not name or not isinstance(arguments, dict):
                    raise HTTPException(status_code=400, detail="Claude gateway tool use is invalid")
                if call_id:
                    tool_names[call_id] = name
                tool_calls.append(
                    {"type": "function", "function": {"name": name, "arguments": arguments}}
                )
            elif block_type == "tool_result" and role == "user":
                call_id = str(block.get("tool_use_id") or "").strip()
                name = tool_names.get(call_id)
                if not name:
                    raise HTTPException(status_code=400, detail="Claude gateway tool result has no matching tool use")
                tool_results.append({"role": "tool", "tool_name": name, "content": _as_text(block.get("content"))})
            elif block_type in {"thinking", "redacted_thinking"}:
                # Ollama Cloud receives no private chain-of-thought from callers.
                continue
            else:
                raise HTTPException(status_code=400, detail=f"Claude gateway content block is unsupported: {block_type}")

        if text_parts or tool_calls or not tool_results:
            message: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
        messages.extend(tool_results)
    return messages


def _ollama_request(payload: dict[str, Any]) -> dict[str, Any]:
    requested_max_tokens = payload.get("max_tokens", 1024)
    try:
        max_tokens = max(1, min(int(requested_max_tokens), 16_384))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Claude gateway max_tokens is invalid") from exc
    result: dict[str, Any] = {
        "model": _model(),
        "messages": _anthropic_messages_to_ollama(payload),
        "stream": False,
        "think": False,
        "options": {"num_predict": max_tokens},
    }
    tools = _anthropic_tools_to_ollama(payload.get("tools"))
    if tools:
        result["tools"] = tools
    return result


def _response_content(message: Any) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        message = {}
    content: list[dict[str, Any]] = []
    text = str(message.get("content") or "")
    if text:
        content.append({"type": "text", "text": text})
    for index, raw_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        arguments = function.get("arguments")
        if name and isinstance(arguments, dict):
            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_{uuid.uuid4().hex}_{index}",
                    "name": name,
                    "input": arguments,
                }
            )
    return content or [{"type": "text", "text": ""}]


def _anthropic_response(data: dict[str, Any]) -> dict[str, Any]:
    message = data.get("message") if isinstance(data, dict) else {}
    content = _response_content(message)
    has_tools = any(block.get("type") == "tool_use" for block in content)
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": _model(),
        "content": content,
        "stop_reason": "tool_use" if has_tools else "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(data.get("prompt_eval_count") or 0),
            "output_tokens": int(data.get("eval_count") or 0),
        },
    }


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def _anthropic_sse(response: dict[str, Any]) -> AsyncIterator[str]:
    message = {**response, "content": [], "stop_reason": None, "stop_sequence": None}
    yield _sse("message_start", {"type": "message_start", "message": message})
    for index, block in enumerate(response["content"]):
        yield _sse(
            "content_block_start",
            {"type": "content_block_start", "index": index, "content_block": {**block, **({"input": {}} if block["type"] == "tool_use" else {})}},
        )
        if block["type"] == "tool_use":
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"], separators=(",", ":"))},
                },
            )
        else:
            yield _sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": block.get("text", "")}},
            )
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": index})
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": response["stop_reason"], "stop_sequence": None},
            "usage": {"output_tokens": response["usage"]["output_tokens"]},
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


async def _call_ollama(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = _api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Ollama Cloud API key is not configured")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)) as client:
            response = await client.post(
                f"{_cloud_url()}/chat",
                headers={"Authorization": f"Bearer {api_key}"},
                json=_ollama_request(payload),
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Ollama Cloud request timed out") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail="Ollama Cloud is unreachable") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Ollama Cloud rejected the request") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Ollama Cloud returned an invalid response")
    return _anthropic_response(data)


def create_app() -> FastAPI:
    app = FastAPI(title="Nexus Ollama Claude Gateway", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok" if _gateway_token() and _api_key() else "not_ready",
            "model": _model(),
            "ready": bool(_gateway_token() and _api_key()),
        }

    @app.post("/v1/messages")
    async def messages(request: Request):
        _require_authentication(request)
        payload = await _read_json_body(request)
        response = await _call_ollama(payload)
        if bool(payload.get("stream")):
            return StreamingResponse(_anthropic_sse(response), media_type="text/event-stream")
        return JSONResponse(response)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"type": "error", "error": {"type": "api_error", "message": str(exc.detail)}},
        )

    return app
