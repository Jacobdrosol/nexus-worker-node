from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    cfg = getattr(request.app.state, "worker_config", {})
    attestation = getattr(request.app.state, "capability_attestation", {})
    return {
        "status": "ok",
        "worker_id": cfg.get("id", "unknown"),
        "enabled_cli_tools": attestation.get("enabled_cli_tools", []),
    }

