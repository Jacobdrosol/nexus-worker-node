import asyncio
import shlex
from typing import Any


class CLIExecutionRejected(ValueError):
    """Raised when a worker is asked to run an undeclared CLI executable."""


def _command_parts(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError as exc:
        raise CLIExecutionRejected(f"Invalid CLI command: {exc}") from exc
    if not parts:
        raise CLIExecutionRejected("CLI command is empty")
    return parts


async def infer(
    command: str,
    params: dict[str, Any],
    *,
    allowed_tools: set[str],
    input_text: str,
) -> dict[str, Any]:
    parts = _command_parts(command)
    executable = parts[0].replace("\\", "/").rsplit("/", 1)[-1]
    if executable not in allowed_tools:
        raise CLIExecutionRejected(f"CLI executable is not enabled on this worker: {executable}")

    timeout_seconds = int(params.get("timeout_seconds", 600) or 600)
    timeout_seconds = max(1, min(timeout_seconds, 3600))
    proc = await asyncio.create_subprocess_exec(
        *parts,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    input_bytes = input_text.encode("utf-8") if input_text else None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=input_bytes),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise CLIExecutionRejected(f"CLI command timed out after {timeout_seconds} seconds") from exc
    return {
        "output": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "returncode": proc.returncode,
        "usage": {},
    }
