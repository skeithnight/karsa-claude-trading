"""Karsa Trading System — Crypto Control API

REST endpoints for ASM lifecycle + emergency controls.
Mounted on crypto bot FastAPI app (port 8444).
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/crypto", tags=["asm", "emergency"])


def _get_app_state(request: Request):
    """Get orchestrator and redis from app.state (set during lifespan)."""
    orch = getattr(request.app.state, "orchestrator", None)
    redis_client = getattr(request.app.state, "redis_client", None)
    if not orch or not redis_client:
        raise HTTPException(503, "Crypto bot not initialized — lifespan hasn't run yet")
    return orch, redis_client


def _make_asm(request: Request):
    """Create ASM instance with all dependencies."""
    orch, redis_client = _get_app_state(request)
    from src.data.cache import CacheManager
    from src.data.bybit_client import BybitClient
    from src.agents.autonomous_session import AutonomousSessionManager
    cache = CacheManager(redis_client)
    bybit = BybitClient(cache)
    return AutonomousSessionManager(orch, redis_client, bybit)


# --- ASM Endpoints ---

@router.get("/asm/status")
async def asm_status():
    """Get ASM state, config, equity, and emergency status."""
    from src.risk import emergency
    import json
    from src.config import settings
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    active = await r.get("karsa:asm:active")
    paused = await r.get("karsa:asm:paused")
    config_raw = await r.get("karsa:asm:config")
    equity = await r.get("karsa:asm:start_equity")
    peak = await r.get("karsa:asm:peak_equity")
    max_dd = await r.get("karsa:asm:max_drawdown")
    em = await emergency.get_status()

    return {
        "active": active == "1",
        "paused": paused == "1",
        "config": json.loads(config_raw) if config_raw else None,
        "starting_equity": float(equity) if equity else None,
        "peak_equity": float(peak) if peak else None,
        "max_drawdown_pct": float(max_dd) if max_dd else None,
        "emergency_stop": em,
    }


class ASMStartRequest(BaseModel):
    risk_pct: float = 70.0
    max_pos: int = 3
    interval_min: int = 15
    duration_min: int = 0


@router.post("/asm/start")
async def asm_start(req: ASMStartRequest, request: Request):
    """Start ASM with given config. Fails if already active."""
    asm = _make_asm(request)
    result = await asm.start(0, {
        "risk_pct": req.risk_pct,
        "max_pos": req.max_pos,
        "interval": req.interval_min,
        "duration_min": req.duration_min,
    })
    import asyncio
    asyncio.create_task(asm._run_loop(0))
    return {"status": "started", "message": result}


@router.post("/asm/stop")
async def asm_stop(request: Request):
    """Stop ASM. Returns final report."""
    asm = _make_asm(request)
    result = await asm.stop()
    return {"status": "stopped", "message": result}


@router.post("/asm/resume")
async def asm_resume(request: Request):
    """Resume paused ASM."""
    asm = _make_asm(request)
    result = await asm.resume()
    return {"status": "resumed", "message": result}


# --- Emergency Endpoints ---

@router.post("/emergency/flatten")
async def emergency_flatten():
    """Activate emergency stop and flatten all positions."""
    from src.risk import emergency
    was_set = await emergency.activate("manual_api", "api")
    return {"status": "activated" if was_set else "already_active", "flatten": was_set}


@router.post("/emergency/resume")
async def emergency_resume():
    """Clear emergency stop. Resume trading."""
    from src.risk import emergency
    await emergency.deactivate("api")
    return {"status": "deactivated", "trading": "resumed"}


@router.get("/emergency/status")
async def emergency_status():
    """Get emergency stop status."""
    from src.risk import emergency
    return await emergency.get_status() or {"active": False}
