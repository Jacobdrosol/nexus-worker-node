import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Dict

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nexus_worker.api import capabilities, health, infer, infer_stream, models
from nexus_worker.config_loader import ConfigLoader
from nexus_worker.hardware.detector import detect_hardware_profile
from nexus_worker.observability import install_observability

logger = logging.getLogger(__name__)

WORKER_CONFIG_PATH = os.environ.get("NEXUS_WORKER_CONFIG_PATH", "nexus_worker/config.yaml.example")
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "15"))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _control_plane_url() -> str:
    return os.environ.get("CONTROL_PLANE_URL", "").strip()


def _control_plane_api_token() -> str:
    return os.environ.get("CONTROL_PLANE_API_TOKEN", "").strip()


def _auto_register_enabled() -> bool:
    return _env_flag("NEXUS_WORKER_AUTO_REGISTER", default=False)


def _cp_headers() -> Dict[str, str]:
    token = _control_plane_api_token()
    if not token:
        return {}
    return {"X-Nexus-API-Key": token}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        worker_config = ConfigLoader.load_yaml(WORKER_CONFIG_PATH)
    except Exception as e:
        logger.warning("Failed to load nexus_worker config from %s: %s", WORKER_CONFIG_PATH, e)
        worker_config = {
            "id": "nexus-worker-standalone",
            "name": "Nexus Worker Standalone",
            "host": "0.0.0.0",
            "port": 8010,
            "status": "offline",
            "enabled": True,
            "capabilities": [],
            "metrics": {},
        }

    app.state.worker_config = worker_config
    worker_id = worker_config.get("id", "nexus-worker-standalone")
    control_plane_url = _control_plane_url()
    auto_register = _auto_register_enabled()

    heartbeat_task: asyncio.Task | None = None
    if auto_register and control_plane_url:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{control_plane_url}/v1/workers",
                    json=worker_config,
                    headers=_cp_headers(),
                )
                resp.raise_for_status()
                logger.info("nexus_worker registered with control plane as %s", worker_id)
            heartbeat_task = asyncio.create_task(_send_heartbeats(worker_id, app))
        except Exception as e:
            logger.warning("nexus_worker registration failed: %s", e)
    else:
        logger.info(
            "nexus_worker running in local-only mode; set NEXUS_WORKER_AUTO_REGISTER=1 and CONTROL_PLANE_URL to enable control-plane registration"
        )

    yield
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _send_heartbeats(worker_id: str, app: FastAPI) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            control_plane_url = _control_plane_url()
            if not control_plane_url:
                continue
            hw = detect_hardware_profile()
            cpu = hw.get("cpu") or {}
            gpus = hw.get("gpus") or []
            gpu_util = [float(g.get("utilization_percent") or 0.0) for g in gpus]
            metrics = {
                "load": float(cpu.get("usage_percent") or 0.0),
                "gpu_utilization": gpu_util,
                "queue_depth": int(getattr(app.state, "inference_inflight", 0) or 0),
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                await client.post(
                    f"{control_plane_url}/v1/workers/{worker_id}/heartbeat",
                    json={"metrics": metrics},
                    headers=_cp_headers(),
                )
        except Exception as e:
            logger.warning("nexus_worker heartbeat failed: %s", e)


def create_app() -> FastAPI:
    app = FastAPI(title="Nexus Worker", version="0.1.0", lifespan=lifespan)
    install_observability(app)
    app.include_router(health.router)
    app.include_router(capabilities.router)
    app.include_router(models.router)
    app.include_router(infer.router)
    app.include_router(infer_stream.router)

    @app.exception_handler(Exception)
    async def _generic_exception_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": type(exc).__name__, "detail": str(exc)})

    return app


app = create_app()
