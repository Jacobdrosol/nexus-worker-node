import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import uvicorn

from nexus_worker.agent import WORKER_CONFIG_PATH
from nexus_worker.bootstrap import bootstrap_worker_node
from nexus_worker.config_loader import ConfigLoader
from nexus_worker.env import load_env_file


def _default_env_file() -> str:
    return ".env"


def _generated_env_file() -> str:
    output_dir = os.environ.get("NEXUS_WORKER_OUTPUT_DIR", "generated/worker-node")
    return str(Path(output_dir) / "nexus-worker.env")


def _load_runtime_env(path: str | None, *, override: bool = True) -> None:
    if path:
        load_env_file(path, override=override)


def _run_server() -> None:
    cfg = {}
    try:
        cfg = ConfigLoader.load_yaml(WORKER_CONFIG_PATH)
    except Exception:
        cfg = {}
    uvicorn.run(
        "nexus_worker.agent:app",
        host=cfg.get("host", "0.0.0.0"),
        port=int(cfg.get("port", int(os.environ.get("NEXUS_WORKER_PORT", "8010")))),
        reload=False,
    )


def _init_command(args: argparse.Namespace) -> None:
    if args.env_file:
        load_env_file(args.env_file, override=True)

    effective = argparse.Namespace(
        output_dir=args.output_dir or os.environ.get("NEXUS_WORKER_OUTPUT_DIR", "generated/worker-node"),
        worker_id=args.worker_id or os.environ.get("NEXUS_WORKER_ID", ""),
        worker_name=args.worker_name or os.environ.get("NEXUS_WORKER_NAME", ""),
        host=args.host or os.environ.get("NEXUS_WORKER_HOST", ""),
        port=int(args.port or os.environ.get("NEXUS_WORKER_PORT", "8010")),
        python=args.python or "",
        ollama_host=args.ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        control_plane_url=args.control_plane_url or os.environ.get("CONTROL_PLANE_URL", ""),
        control_plane_api_token=args.control_plane_api_token or os.environ.get("CONTROL_PLANE_API_TOKEN", ""),
        generate_token=args.generate_token,
        pull_ollama_model=args.pull_ollama_model or [],
        install_service=args.install_service,
        verify=args.verify,
        enable_control_plane_registration=(
            args.enable_control_plane_registration
            or os.environ.get("NEXUS_WORKER_AUTO_REGISTER", "0").strip().lower() in {"1", "true", "yes", "on"}
        ),
    )
    summary = asyncio.run(bootstrap_worker_node(effective))
    print(__import__("json").dumps(summary, indent=2))


def _run_command(args: argparse.Namespace) -> None:
    env_file = args.env_file
    if env_file is None and Path(_default_env_file()).exists():
        env_file = _default_env_file()
    _load_runtime_env(env_file, override=True)
    generated_env = _generated_env_file()
    if Path(generated_env).exists():
        _load_runtime_env(generated_env, override=True)
    _run_server()


def _install_service_command(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    if os.name == "nt":
        script = output_dir / "install-service.ps1"
        command = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    else:
        script = output_dir / "install-service.sh"
        command = ["sh", str(script)]
    raise_code = subprocess.run(command, check=False).returncode
    raise SystemExit(raise_code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Nexus Worker CLI")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Generate worker config, env, and service assets.")
    init_parser.add_argument("--env-file", default=_default_env_file(), help="Optional env file to read defaults from.")
    init_parser.add_argument("--output-dir", default="", help="Directory for generated worker assets.")
    init_parser.add_argument("--worker-id", default="", help="Stable worker identifier.")
    init_parser.add_argument("--worker-name", default="", help="Human-readable worker name.")
    init_parser.add_argument("--host", default="", help="Worker host/IP advertised locally.")
    init_parser.add_argument("--port", default=0, type=int, help="Worker listen port.")
    init_parser.add_argument("--python", default="", help="Python executable to use for the background service.")
    init_parser.add_argument("--ollama-host", default="", help="Local Ollama host for model discovery.")
    init_parser.add_argument("--control-plane-url", default="", help="Control plane base URL.")
    init_parser.add_argument("--control-plane-api-token", default="", help="Control plane API token.")
    init_parser.add_argument("--generate-token", action="store_true", help="Generate a token if one is not provided.")
    init_parser.add_argument("--pull-ollama-model", action="append", default=[], help="Ollama model to pull during bootstrap. May be passed multiple times.")
    init_parser.add_argument("--install-service", action="store_true", help="Attempt to install and start the generated background service.")
    init_parser.add_argument("--verify", action="store_true", help="Verify the local worker endpoints after bootstrap.")
    init_parser.add_argument("--enable-control-plane-registration", action="store_true", help="Enable control-plane registration on startup.")
    init_parser.set_defaults(func=_init_command)

    run_parser = subparsers.add_parser("run", help="Run the standalone worker.")
    run_parser.add_argument("--env-file", default=None, help="Optional env file to load before starting.")
    run_parser.set_defaults(func=_run_command)

    install_parser = subparsers.add_parser("install-service", help="Install the generated background service.")
    install_parser.add_argument("--output-dir", default="generated/worker-node", help="Directory containing generated service assets.")
    install_parser.set_defaults(func=_install_service_command)

    args = parser.parse_args()
    if not args.command:
        _run_command(argparse.Namespace(env_file=None))
        return
    args.func(args)


if __name__ == "__main__":
    main()
