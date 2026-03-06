"""Bootstrap utility for preparing a standalone nexus_worker node."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from nexus_worker.manager.cli_tools import discover_cli_tools
from nexus_worker.manager.local_models import discover_local_models


def _slugify(value: str) -> str:
    cleaned = []
    for ch in (value or "").strip().lower():
        cleaned.append(ch if ch.isalnum() else "-")
    out = "".join(cleaned).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "nexus-worker"


def _platform_id() -> str:
    system = platform.system().lower()
    if "windows" in system:
        return "windows"
    if "darwin" in system:
        return "macos"
    return "linux"


def _default_python() -> str:
    return sys.executable or shutil.which("python3") or shutil.which("python") or "python"


def _hostname() -> str:
    return socket.gethostname() or "localhost"


def _build_capabilities(local_models: dict[str, Any], cli_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for item in local_models.get("models", []):
        provider = str(item.get("provider") or "").strip()
        name = str(item.get("name") or "").strip()
        if not provider or not name:
            continue
        grouped.setdefault(("llm", provider), set()).add(name)
    if cli_tools:
        grouped.setdefault(("tool", "cli"), set()).update(tool["name"] for tool in cli_tools if tool.get("name"))
    out: list[dict[str, Any]] = []
    for (cap_type, provider), models in sorted(grouped.items()):
        out.append(
            {
                "type": cap_type,
                "provider": provider,
                "models": sorted(models),
            }
        )
    return out


def _build_worker_config(
    *,
    worker_id: str,
    worker_name: str,
    host: str,
    port: int,
    ollama_host: str,
    local_models: dict[str, Any],
    cli_tools: list[dict[str, Any]],
    control_plane_url: str,
) -> dict[str, Any]:
    return {
        "id": worker_id,
        "name": worker_name,
        "host": host,
        "port": port,
        "status": "offline",
        "enabled": True,
        "ollama_host": ollama_host,
        "control_plane_url": control_plane_url,
        "capabilities": _build_capabilities(local_models, cli_tools),
        "cli_tools": cli_tools,
        "metrics": {},
    }


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_env_file(
    path: Path,
    *,
    control_plane_url: str,
    control_plane_api_token: str,
    config_path: Path,
    auto_register: bool,
) -> None:
    lines = [
        f"NEXUS_WORKER_CONFIG_PATH={config_path.as_posix()}",
        f"CONTROL_PLANE_URL={control_plane_url}",
        f"CONTROL_PLANE_API_TOKEN={control_plane_api_token}",
        f"NEXUS_WORKER_AUTO_REGISTER={1 if auto_register else 0}",
        "HEARTBEAT_INTERVAL=15",
        "NEXUS_WORKER_CLOUD_CONTEXT_POLICY=redact",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _unix_runner_text(workdir: Path, env_path: Path, python_bin: str) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env sh",
            "set -eu",
            f"set -a; . \"{env_path.as_posix()}\"; set +a",
            f"cd \"{workdir.as_posix()}\"",
            f"exec \"{python_bin}\" -m nexus_worker",
            "",
        ]
    )


def _linux_service_text(service_name: str, workdir: Path, env_path: Path, python_bin: str) -> str:
    return "\n".join(
        [
            "[Unit]",
            f"Description={service_name}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={workdir.as_posix()}",
            f"EnvironmentFile={env_path.as_posix()}",
            f"ExecStart={python_bin} -m nexus_worker",
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def _macos_plist_text(
    service_name: str,
    workdir: Path,
    control_plane_url: str,
    control_plane_api_token: str,
    python_bin: str,
) -> str:
    return "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">",
            "<plist version=\"1.0\">",
            "<dict>",
            f"  <key>Label</key><string>{service_name}</string>",
            "  <key>ProgramArguments</key>",
            "  <array>",
            f"    <string>{python_bin}</string>",
            "    <string>-m</string>",
            "    <string>nexus_worker</string>",
            "  </array>",
            f"  <key>WorkingDirectory</key><string>{workdir.as_posix()}</string>",
            "  <key>RunAtLoad</key><true/>",
            "  <key>KeepAlive</key><true/>",
            "  <key>EnvironmentVariables</key>",
            "  <dict>",
            f"    <key>NEXUS_WORKER_CONFIG_PATH</key><string>{(workdir / 'nexus-worker.yaml').as_posix()}</string>",
            f"    <key>CONTROL_PLANE_URL</key><string>{control_plane_url}</string>",
            f"    <key>CONTROL_PLANE_API_TOKEN</key><string>{control_plane_api_token}</string>",
            "    <key>HEARTBEAT_INTERVAL</key><string>15</string>",
            "    <key>NEXUS_WORKER_CLOUD_CONTEXT_POLICY</key><string>redact</string>",
            "  </dict>",
            "</dict>",
            "</plist>",
            "",
        ]
    )


def _windows_runner_text(workdir: Path, python_bin: str) -> str:
    return "\r\n".join(
        [
            "@echo off",
            f"set NEXUS_WORKER_CONFIG_PATH={str((workdir / 'nexus-worker.yaml'))}",
            f"if exist \"{str(workdir / 'nexus-worker.env')}\" (",
            f"  for /f \"usebackq tokens=1,* delims==\" %%A in (\"{str(workdir / 'nexus-worker.env')}\") do set %%A=%%B",
            ")",
            f"cd /d \"{str(workdir)}\"",
            f"\"{python_bin}\" -m nexus_worker",
            "",
        ]
    )


def _windows_install_ps1(service_name: str, workdir: Path) -> str:
    cmd_path = workdir / "run-nexus-worker.cmd"
    return "\n".join(
        [
            f"$TaskName = '{service_name}'",
            f"$Command = '{str(cmd_path)}'",
            "$Action = New-ScheduledTaskAction -Execute $Command",
            "$Trigger = New-ScheduledTaskTrigger -AtStartup",
            "$Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest",
            "Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Force",
            "Start-ScheduledTask -TaskName $TaskName",
            "",
        ]
    )


def _install_scripts(
    platform_id: str,
    service_name: str,
    workdir: Path,
    env_path: Path,
    python_bin: str,
    control_plane_url: str,
    control_plane_api_token: str,
) -> dict[str, str]:
    out: dict[str, str] = {}
    if platform_id == "linux":
        runner_path = workdir / "run-nexus-worker.sh"
        runner_path.write_text(_unix_runner_text(workdir, env_path, python_bin), encoding="utf-8")
        runner_path.chmod(0o755)
        service_path = workdir / f"{service_name}.service"
        service_path.write_text(_linux_service_text(service_name, workdir, env_path, python_bin), encoding="utf-8")
        install_path = workdir / "install-service.sh"
        install_path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env sh",
                    "set -eu",
                    f"sudo cp \"{service_path.as_posix()}\" \"/etc/systemd/system/{service_name}.service\"",
                    "sudo systemctl daemon-reload",
                    f"sudo systemctl enable --now {service_name}.service",
                    f"sudo systemctl status {service_name}.service --no-pager",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        install_path.chmod(0o755)
        out["runner_script"] = runner_path.name
        out["service_file"] = service_path.name
        out["install_script"] = install_path.name
    elif platform_id == "macos":
        runner_path = workdir / "run-nexus-worker.sh"
        runner_path.write_text(_unix_runner_text(workdir, env_path, python_bin), encoding="utf-8")
        runner_path.chmod(0o755)
        plist_path = workdir / f"{service_name}.plist"
        plist_path.write_text(
            _macos_plist_text(service_name, workdir, control_plane_url, control_plane_api_token, python_bin),
            encoding="utf-8",
        )
        install_path = workdir / "install-service.sh"
        install_path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env sh",
                    "set -eu",
                    f"mkdir -p \"$HOME/Library/LaunchAgents\"",
                    f"cp \"{plist_path.as_posix()}\" \"$HOME/Library/LaunchAgents/{service_name}.plist\"",
                    f"launchctl unload \"$HOME/Library/LaunchAgents/{service_name}.plist\" >/dev/null 2>&1 || true",
                    f"launchctl load \"$HOME/Library/LaunchAgents/{service_name}.plist\"",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        install_path.chmod(0o755)
        out["runner_script"] = runner_path.name
        out["service_file"] = plist_path.name
        out["install_script"] = install_path.name
    else:
        cmd_path = workdir / "run-nexus-worker.cmd"
        cmd_path.write_text(_windows_runner_text(workdir, python_bin), encoding="utf-8")
        install_path = workdir / "install-service.ps1"
        install_path.write_text(_windows_install_ps1(service_name, workdir), encoding="utf-8")
        out["runner_script"] = cmd_path.name
        out["install_script"] = install_path.name
    return out


def _service_install_command(platform_id: str, output_dir: Path, service_assets: dict[str, str]) -> list[str]:
    install_script = service_assets["install_script"]
    if platform_id == "windows":
        return [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(output_dir / install_script),
        ]
    return ["sh", str(output_dir / install_script)]


def _manual_run_command(platform_id: str, output_dir: Path, service_assets: dict[str, str]) -> list[str]:
    runner_script = service_assets["runner_script"]
    if platform_id == "windows":
        return [str(output_dir / runner_script)]
    return ["sh", str(output_dir / runner_script)]


def _run_command(command: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "command": command, "detail": str(exc)}
    detail = (proc.stdout or proc.stderr or "").strip()[:1000]
    return {
        "ok": proc.returncode == 0,
        "command": command,
        "returncode": proc.returncode,
        "detail": detail,
    }


async def _verify_local_worker(host: str, port: int) -> dict[str, Any]:
    base_url = f"http://127.0.0.1:{port}"
    results: dict[str, Any] = {"base_url": base_url}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for path in ("/health", "/capabilities"):
            try:
                resp = await client.get(f"{base_url}{path}")
                results[path] = {
                    "ok": resp.status_code == 200,
                    "status_code": resp.status_code,
                }
            except Exception as exc:
                results[path] = {
                    "ok": False,
                    "detail": str(exc),
                }
    return results


def _pull_ollama_models(ollama_host: str, models: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        return [{"model": name, "ok": False, "detail": "ollama executable not found"} for name in models]
    for name in models:
        try:
            proc = subprocess.run(
                [ollama_bin, "pull", name],
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
                env={**os.environ, "OLLAMA_HOST": ollama_host},
            )
            ok = proc.returncode == 0
            detail = (proc.stdout or proc.stderr or "").strip()[:400]
        except Exception as exc:
            ok = False
            detail = str(exc)
        results.append({"model": name, "ok": ok, "detail": detail})
    return results


async def bootstrap_worker_node(args: argparse.Namespace) -> dict[str, Any]:
    platform_id = _platform_id()
    worker_id = args.worker_id or f"{_slugify(args.worker_name or _hostname())}-{platform_id}"
    worker_name = args.worker_name or f"{platform_id.capitalize()} Worker"
    host = args.host or _hostname()
    port = int(args.port)
    python_bin = args.python or _default_python()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    control_plane_token = args.control_plane_api_token or ""
    if args.generate_token and not control_plane_token:
        control_plane_token = secrets.token_urlsafe(24)

    local_models = await discover_local_models({"ollama_host": args.ollama_host})
    cli_tools = discover_cli_tools()
    worker_config = _build_worker_config(
        worker_id=worker_id,
        worker_name=worker_name,
        host=host,
        port=port,
        ollama_host=args.ollama_host,
        local_models=local_models,
        cli_tools=cli_tools,
        control_plane_url=args.control_plane_url,
    )

    config_path = output_dir / "nexus-worker.yaml"
    env_path = output_dir / "nexus-worker.env"
    _write_yaml(config_path, worker_config)
    _write_env_file(
        env_path,
        control_plane_url=args.control_plane_url,
        control_plane_api_token=control_plane_token,
        config_path=config_path,
        auto_register=bool(args.enable_control_plane_registration),
    )

    service_name = f"nexus-worker-{worker_id}"
    service_assets = _install_scripts(
        platform_id,
        service_name,
        output_dir,
        env_path,
        python_bin,
        args.control_plane_url,
        control_plane_token,
    )
    pull_results = _pull_ollama_models(args.ollama_host, args.pull_ollama_model or []) if args.pull_ollama_model else []
    install_result: dict[str, Any] | None = None
    verify_result: dict[str, Any] | None = None
    if args.install_service:
        install_result = _run_command(_service_install_command(platform_id, output_dir, service_assets))
    if args.verify:
        verify_result = await _verify_local_worker(host, port)

    manual_run_command = _manual_run_command(platform_id, output_dir, service_assets)

    summary = {
        "worker_id": worker_id,
        "worker_name": worker_name,
        "platform": platform_id,
        "control_plane_url": args.control_plane_url,
        "control_plane_api_token_configured": bool(control_plane_token),
        "control_plane_registration_enabled": bool(args.enable_control_plane_registration),
        "config_path": str(config_path),
        "env_path": str(env_path),
        "service_name": service_name,
        "service_assets": service_assets,
        "manual_run_command": manual_run_command,
        "discovered_models": local_models,
        "discovered_cli_tools": cli_tools,
        "ollama_pull_results": pull_results,
        "service_install_result": install_result,
        "verify_result": verify_result,
        "next_steps": [
            f"Run {' '.join(manual_run_command)} to start the worker directly.",
            f"Run {' '.join(_service_install_command(platform_id, output_dir, service_assets))} to install it as a background service.",
            f"Verify local worker health at http://127.0.0.1:{port}/health.",
        ],
    }
    if not args.enable_control_plane_registration:
        summary["next_steps"].append(
            "Edit generated/worker-node/nexus-worker.env and set NEXUS_WORKER_AUTO_REGISTER=1 when you are ready to connect this worker to a control plane."
        )
    (output_dir / "bootstrap-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap a standalone nexus_worker node.")
    parser.add_argument("--output-dir", default="generated/worker-node", help="Directory for generated worker assets.")
    parser.add_argument("--worker-id", default="", help="Stable worker identifier.")
    parser.add_argument("--worker-name", default="", help="Human-readable worker name.")
    parser.add_argument("--host", default="", help="Worker host/IP advertised to control plane.")
    parser.add_argument("--port", default=8010, type=int, help="Worker listen port.")
    parser.add_argument("--python", default="", help="Python executable to use for the background service.")
    parser.add_argument("--ollama-host", default="http://localhost:11434", help="Local Ollama host for model discovery.")
    parser.add_argument("--control-plane-url", default="", help="Control plane base URL.")
    parser.add_argument("--control-plane-api-token", default="", help="Control plane API token.")
    parser.add_argument("--generate-token", action="store_true", help="Generate a token if one is not provided.")
    parser.add_argument("--pull-ollama-model", action="append", default=[], help="Ollama model to pull during bootstrap. May be passed multiple times.")
    parser.add_argument("--install-service", action="store_true", help="Attempt to install and start the generated background service.")
    parser.add_argument("--verify", action="store_true", help="Verify the local worker endpoints after bootstrap.")
    parser.add_argument("--enable-control-plane-registration", action="store_true", help="Write bootstrap env so the worker auto-registers with the control plane on startup.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(bootstrap_worker_node(args))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
