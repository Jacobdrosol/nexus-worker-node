from pathlib import Path

import os

from nexus_worker.env import load_env_file, parse_env_file


def test_parse_env_file_reads_key_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nNEXUS_WORKER_NAME=My Worker\nNEXUS_WORKER_PORT=8010\n",
        encoding="utf-8",
    )

    env = parse_env_file(str(env_file))

    assert env["NEXUS_WORKER_NAME"] == "My Worker"
    assert env["NEXUS_WORKER_PORT"] == "8010"


def test_load_env_file_can_override_existing_values(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("NEXUS_WORKER_PORT=8015\n", encoding="utf-8")

    monkeypatch.setenv("NEXUS_WORKER_PORT", "8010")
    load_env_file(str(env_file), override=True)

    assert os.environ["NEXUS_WORKER_PORT"] == "8015"
