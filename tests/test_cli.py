from __future__ import annotations

import argparse
import os
from pathlib import Path

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


def test_run_server_uses_current_config_path(tmp_path, monkeypatch):
    config_path = tmp_path / "worker.yaml"
    config_path.write_text("host: 0.0.0.0\nport: 8013\n", encoding="utf-8")

    monkeypatch.setenv("NEXUS_WORKER_CONFIG_PATH", str(config_path))

    captured: dict[str, object] = {}

    class _DummyAgent:
        app = object()

    monkeypatch.setattr(cli.importlib, "reload", lambda module: _DummyAgent)
    monkeypatch.setitem(__import__("sys").modules, "nexus_worker.agent", _DummyAgent)

    def _fake_uvicorn_run(app, host, port, reload):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload

    monkeypatch.setattr(cli.uvicorn, "run", _fake_uvicorn_run)

    cli._run_server()

    assert captured["app"] is _DummyAgent.app
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8013
    assert captured["reload"] is False
