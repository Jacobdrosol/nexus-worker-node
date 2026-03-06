# Nexus Worker Node

`nexus-worker-node` is the standalone worker runtime for NexusAI.

Use this project on any machine that should run local models, CLI tools, or background task execution without cloning the full NexusAI application repo.

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

## Quick Start

Generate a worker config and service assets:

```bash
nexus-worker-bootstrap \
  --control-plane-url http://YOUR_CONTROL_PLANE_HOST:8000 \
  --control-plane-api-token YOUR_SHARED_TOKEN \
  --worker-name "My Worker Node"
```

That writes:

- `generated/worker-node/nexus-worker.yaml`
- `generated/worker-node/nexus-worker.env`
- `generated/worker-node/bootstrap-summary.json`
- OS-specific background-service install scripts

Then install the generated service:

### Linux

```bash
cd generated/worker-node
sh ./install-service.sh
```

### macOS

```bash
cd generated/worker-node
sh ./install-service.sh
```

### Windows

```powershell
cd .\generated\worker-node
.\install-service.ps1
```

## Manual Run

If you want to run it directly first:

```bash
python -m nexus_worker
```

or:

```bash
nexus-worker
```

Default runtime assumptions:

- control plane URL: `http://localhost:8000`
- worker port: `8010`
- cloud context policy: `redact`

## Configuration

Main environment variables:

- `NEXUS_WORKER_CONFIG_PATH`
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
