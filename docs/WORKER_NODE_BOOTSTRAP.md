# Worker Node Bootstrap

This guide prepares a standalone `nexus_worker` node that:

- discovers local Ollama models and common CLI tools
- writes a worker config and env file
- generates background-service install assets for Linux, macOS, or Windows
- can later connect back to the NexusAI control plane when explicitly enabled

## 1. Prerequisites

- Python 3.11+
- NexusAI checked out on the worker node
- network reachability from worker node to control plane
- optional local runtimes already installed:
  - Ollama
  - Codex CLI
  - GitHub CLI
  - Docker

## 2. Create Your Local Env File

Copy the example file:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit `.env` for that machine.

Recommended first local setup:

```env
NEXUS_WORKER_NAME=My Worker Node
NEXUS_WORKER_PORT=8010
NEXUS_WORKER_AUTO_REGISTER=0
CONTROL_PLANE_URL=
CONTROL_PLANE_API_TOKEN=
NEXUS_WORKER_CLOUD_CONTEXT_POLICY=redact
```

## 2b. Pick the Right Control-Plane URL

When you later enable registration, `CONTROL_PLANE_URL` must be the base URL for the control-plane API as seen from the worker machine.

Use:

- a private address like `http://100.81.64.82:8000` if the worker can reach that server directly
- a public hostname like `https://api.example.com` only if that hostname exposes `/v1/*`

Do not assume the dashboard hostname is also the API hostname.

The correct base URL must make these routes reachable:

- `<BASE_URL>/v1/workers`
- `<BASE_URL>/v1/bots`
- `<BASE_URL>/v1/projects`

Interpretation:

- `200` means correct
- `401` or `403` usually means the URL is correct and auth is the problem
- `404` means the URL or path is wrong

## 3. Run the Bootstrap Utility

From the repo root on the worker node:

```bash
nexus-worker init
```

Optional:

```bash
nexus-worker init \
  --pull-ollama-model llama3.1:8b \
  --pull-ollama-model nomic-embed-text
```

Generated assets:

- `generated/worker-node/nexus-worker.yaml`
- `generated/worker-node/nexus-worker.env`
- `generated/worker-node/bootstrap-summary.json`
- service install assets for the current OS
- direct runner script for the current OS

All generated runtime files are written under `generated/worker-node/`, which is intended to remain local and is ignored by Git.

Useful optional flags:

- `--install-service`: attempt to install and start the generated service
- `--verify`: check local `/health` and `/capabilities` after bootstrap
- `--enable-control-plane-registration`: opt in to control-plane registration on startup

Direct-run test before installing a service:

Linux/macOS:

```bash
sh ./generated/worker-node/run-nexus-worker.sh
```

Windows:

```powershell
.\generated\worker-node\run-nexus-worker.cmd
```

## 4. What Gets Discovered

The bootstrap utility detects:

- Ollama local models
- common CLI tools:
  - `codex`
  - `gh`
  - `git`
  - `python`
  - `node`
  - `npm`
  - `docker`
  - `ollama`

CLI metadata includes:

- executable path
- version string when available
- whether the tool usually needs approval/auth setup first
- approval/setup hints

This metadata is written into `nexus-worker.yaml` and exposed by `GET /capabilities`.

By default, bootstrap writes `NEXUS_WORKER_AUTO_REGISTER=0` so the worker remains local-only until you explicitly enable registration.

## 5. Install as a Background Service

### Linux

The bootstrap output includes:

- `nexus-worker-<worker-id>.service`
- `install-service.sh`

Run:

```bash
nexus-worker install-service
```

### macOS

The bootstrap output includes:

- `nexus-worker-<worker-id>.plist`
- `install-service.sh`

Run:

```bash
nexus-worker install-service
```

### Windows

The bootstrap output includes:

- `run-nexus-worker.cmd`
- `install-service.ps1`

Run:

```powershell
nexus-worker install-service
```

Windows uses a logon scheduled task for the current user by default. That avoids requiring `SYSTEM` access for first-time setup.

## 6. Verify the Worker

After the service starts, verify:

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/capabilities
```

Then in NexusAI:

- open `Workers`
- confirm the node is registered and online
- inspect worker capabilities

## 7. Current Scope

This bootstrap flow does:

- background service install assets
- worker config/env generation
- CLI/tool discovery
- optional Ollama pulls

It does not yet provide:

- full UI-driven remote worker installation
- CLI approval policy enforcement
- tool-specific auth/setup wizards

Those should be built on top of the generated CLI metadata and worker capability reporting in a later pass.
