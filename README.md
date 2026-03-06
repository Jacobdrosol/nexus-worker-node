# Nexus Worker Node

`nexus-worker-node` is the standalone worker runtime for NexusAI.

Use this project on any machine that should run local models, CLI tools, or background task execution without cloning the full NexusAI application repo.

By default, the worker starts in local-only mode and does not contact any control plane until you explicitly enable registration.

## What It Does

- runs a standalone worker API
- registers itself with the NexusAI control plane
- sends heartbeat and hardware metrics
- discovers local Ollama models
- discovers installed CLI tools such as `codex`, `gh`, `git`, `docker`, and `ollama`
- generates background-service install assets for Linux, macOS, and Windows

## Project Layout

- `nexus_worker/`: worker runtime, bootstrap utility, service logic
- `docs/WORKER_NODE_BOOTSTRAP.md`: full install and service guide
- `tests/`: worker-only tests
- `pyproject.toml`: package metadata and CLI entrypoints

## Install

### Development install

```bash
pip install -e .[test]
```

### Standard install

```bash
pip install .
```

## First-Time Setup

1. Copy the example env file.

Linux/macOS:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

2. Edit `.env` for that machine.

Most users should set:

- `NEXUS_WORKER_NAME`
- `NEXUS_WORKER_PORT`
- `OLLAMA_HOST` if Ollama is not on the default port

Leave these blank unless you are ready to connect this worker to NexusAI:

- `CONTROL_PLANE_URL`
- `CONTROL_PLANE_API_TOKEN`

Keep this off for local-only startup:

- `NEXUS_WORKER_AUTO_REGISTER=0`

## Quick Start

From a fresh machine, the shortest path is:

```bash
pip install .
cp .env.example .env
# edit .env
nexus-worker init
```

Generate a worker config and service assets:

```bash
nexus-worker init
```

That writes:

- `generated/worker-node/nexus-worker.yaml`
- `generated/worker-node/nexus-worker.env`
- `generated/worker-node/bootstrap-summary.json`
- OS-specific background-service install scripts

Bootstrap also writes a direct runner script so you can test the worker before installing it as a service.
It writes `NEXUS_WORKER_AUTO_REGISTER=0` by default, so the worker stays local until you opt in.

### Start it directly today

Linux/macOS:

```bash
nexus-worker run
```

Windows:

```powershell
nexus-worker run
```

### One-command bootstrap extras

You can also ask the bootstrap utility to attempt service installation and local verification:

```bash
nexus-worker init \
  --install-service \
  --verify
```

Notes:

- `--install-service` may require elevated privileges depending on OS.
- `--verify` checks `http://127.0.0.1:<port>/health` and `/capabilities`.

### Enable control-plane registration when you are ready

Either edit `generated/worker-node/nexus-worker.env` and set:

```env
NEXUS_WORKER_AUTO_REGISTER=1
CONTROL_PLANE_URL=http://YOUR_CONTROL_PLANE_HOST:8000
CONTROL_PLANE_API_TOKEN=YOUR_SHARED_TOKEN
```

or generate it that way up front:

```bash
nexus-worker init \
  --control-plane-url http://YOUR_CONTROL_PLANE_HOST:8000 \
  --control-plane-api-token YOUR_SHARED_TOKEN \
  --enable-control-plane-registration
```

Then install the generated service:

### Linux

```bash
nexus-worker install-service
```

### macOS

```bash
nexus-worker install-service
```

### Windows

```powershell
nexus-worker install-service
```

## Manual Run

If you want to run it directly first:

```bash
nexus-worker run
```

or:

```bash
python -m nexus_worker run
```

Default runtime assumptions:

- control plane registration: disabled
- control plane URL: unset unless you configure it
- worker port: `8010`
- cloud context policy: `redact`

## Configuration

Main environment variables:

- `NEXUS_WORKER_NAME`
- `NEXUS_WORKER_CONFIG_PATH`
- `NEXUS_WORKER_OUTPUT_DIR`
- `CONTROL_PLANE_URL`
- `CONTROL_PLANE_API_TOKEN`
- `HEARTBEAT_INTERVAL`
- `NEXUS_WORKER_CLOUD_CONTEXT_POLICY`
- `VLLM_MODELS`

Base config example:

- `nexus_worker/config.yaml.example`

## Verify

After the worker starts:

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/capabilities
```

Then in NexusAI:

- open `Workers`
- confirm the node is online
- inspect discovered models and CLI tools

## Development

Run tests:

```bash
pytest -q
```

## Documentation

- bootstrap and service install guide: `docs/WORKER_NODE_BOOTSTRAP.md`
- package details: `nexus_worker/README.md`
