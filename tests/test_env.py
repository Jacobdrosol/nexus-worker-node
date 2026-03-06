from pathlib import Path

from nexus_worker.env import parse_env_file


def test_parse_env_file_reads_key_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nNEXUS_WORKER_NAME=My Worker\nNEXUS_WORKER_PORT=8010\n",
        encoding="utf-8",
    )

    env = parse_env_file(str(env_file))

    assert env["NEXUS_WORKER_NAME"] == "My Worker"
    assert env["NEXUS_WORKER_PORT"] == "8010"
