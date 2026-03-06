from __future__ import annotations

import argparse
import os

from nexus_worker import __main__ as cli


def test_run_command_loads_project_env_then_generated_env(tmp_path, monkeypatch):
    project_env = tmp_path / ".env"
    generated_dir = tmp_path / "generated" / "worker-node"
    generated_dir.mkdir(parents=True)
    generated_env = generated_dir / "nexus-worker.env"

    project_env.write_text(
        "\n".join(
            [
                "NEXUS_WORKER_OUTPUT_DIR=generated/worker-node",
                "NEXUS_WORKER_PORT=8011",
            ]
        ),
        encoding="utf-8",
    )
    generated_env.write_text(
        "\n".join(
            [
                f"NEXUS_WORKER_CONFIG_PATH={(generated_dir / 'nexus-worker.yaml').as_posix()}",
                "NEXUS_WORKER_PORT=8012",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NEXUS_WORKER_PORT", raising=False)
    monkeypatch.delenv("NEXUS_WORKER_OUTPUT_DIR", raising=False)

    called = {"ok": False}

    def _fake_run_server() -> None:
        called["ok"] = True

    monkeypatch.setattr(cli, "_run_server", _fake_run_server)

    cli._run_command(argparse.Namespace(env_file=None))

    assert called["ok"] is True
    assert os.environ["NEXUS_WORKER_PORT"] == "8012"
