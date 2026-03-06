"""Environment file helpers for the standalone worker CLI."""

from __future__ import annotations

import os
from pathlib import Path


def parse_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    file_path = Path(path)
    if not file_path.exists():
        return env
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def load_env_file(path: str, *, override: bool = False) -> dict[str, str]:
    env = parse_env_file(path)
    for key, value in env.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return env
