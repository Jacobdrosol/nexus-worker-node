from unittest.mock import AsyncMock, patch

import pytest

from nexus_worker.backends import cli_backend


@pytest.mark.anyio
async def test_cli_backend_executes_only_declared_tool_without_a_shell():
    process = AsyncMock()
    process.communicate = AsyncMock(return_value=(b"ok\n", b""))
    process.returncode = 0

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
        result = await cli_backend.infer(
            command="claude -p 'review this change'",
            params={},
            allowed_tools={"claude"},
            input_text="user:\nSummarize the change.",
        )

    spawn.assert_awaited_once_with(
        "claude",
        "-p",
        "review this change",
        stdin=__import__("asyncio").subprocess.PIPE,
        stdout=__import__("asyncio").subprocess.PIPE,
        stderr=__import__("asyncio").subprocess.PIPE,
    )
    process.communicate.assert_awaited_once_with(input=b"user:\nSummarize the change.")
    assert result["output"] == "ok\n"
    assert result["returncode"] == 0


@pytest.mark.anyio
async def test_cli_backend_rejects_undeclared_executable():
    with pytest.raises(cli_backend.CLIExecutionRejected, match="not enabled"):
        await cli_backend.infer(
            command="rm -rf /tmp/example",
            params={},
            allowed_tools={"claude"},
            input_text="",
        )
