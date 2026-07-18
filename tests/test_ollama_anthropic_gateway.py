import pytest
from fastapi import HTTPException

from nexus_worker.gateways import ollama_anthropic as gateway


def test_translates_anthropic_tool_conversation_to_ollama_messages():
    messages = gateway._anthropic_messages_to_ollama(
        {
            "system": "Review only.",
            "messages": [
                {"role": "user", "content": "Inspect the repository."},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_file",
                            "input": {"path": "src/app.py"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "print('ok')"}
                    ],
                },
            ],
        }
    )

    assert messages == [
        {"role": "system", "content": "Review only."},
        {"role": "user", "content": "Inspect the repository."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "read_file", "arguments": {"path": "src/app.py"}},
                }
            ],
        },
        {"role": "tool", "tool_name": "read_file", "content": "print('ok')"},
    ]


def test_rejects_tool_result_without_known_tool_use():
    with pytest.raises(HTTPException, match="matching tool use"):
        gateway._anthropic_messages_to_ollama(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "unknown", "content": "nope"}],
                    }
                ]
            }
        )


def test_translates_developer_messages_to_ollama_system_messages():
    messages = gateway._anthropic_messages_to_ollama(
        {
            "messages": [
                {"role": "developer", "content": "Use the configured repository only."},
                {"role": "user", "content": "Review this change."},
            ]
        }
    )

    assert messages == [
        {"role": "system", "content": "Use the configured repository only."},
        {"role": "user", "content": "Review this change."},
    ]


def test_translates_ollama_tool_calls_to_anthropic_response(monkeypatch):
    monkeypatch.setenv("NEXUS_OLLAMA_CLAUDE_GATEWAY_MODEL", "glm-5.2:cloud")
    response = gateway._anthropic_response(
        {
            "message": {
                "content": "I will inspect it.",
                "tool_calls": [
                    {"function": {"name": "read_file", "arguments": {"path": "src/app.py"}}}
                ],
            },
            "prompt_eval_count": 12,
            "eval_count": 8,
        }
    )

    assert response["model"] == "glm-5.2:cloud"
    assert response["stop_reason"] == "tool_use"
    assert response["content"][0] == {"type": "text", "text": "I will inspect it."}
    assert response["content"][1]["type"] == "tool_use"
    assert response["content"][1]["name"] == "read_file"
    assert response["content"][1]["input"] == {"path": "src/app.py"}
