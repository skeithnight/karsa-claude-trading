"""Karsa Trading System — Crypto Control API

REST endpoints for ASM lifecycle + emergency controls.
Mounted on crypto bot FastAPI app (port 8444).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/crypto", tags=["asm", "emergency"])


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
async def asm_start(req: ASMStartRequest):
    """Start ASM with given config. Fails if already active."""
    from src.bot.crypto_main import telegram_app
    if not telegram_app or not telegram_app.bot_data.get("orchestrator"):
        raise HTTPException(503, "Crypto bot not initialized")

    orch = telegram_app.bot_data["orchestrator"]
    redis_client = telegram_app.bot_data["redis_client"]
    from src.data.bybit_client import BybitClient
    bybit = BybitClient()
    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, redis_client, bybit)

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
async def asm_stop():
    """Stop ASM. Returns final report."""
    from src.bot.crypto_main import telegram_app
    if not telegram_app or not telegram_app.bot_data.get("orchestrator"):
        raise HTTPException(503, "Crypto bot not initialized")

    orch = telegram_app.bot_data["orchestrator"]
    redis_client = telegram_app.bot_data["redis_client"]
    from src.data.bybit_client import BybitClient
    bybit = BybitClient()
    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, redis_client, bybit)

    result = await asm.stop()
    return {"status": "stopped", "message": result}


@router.post("/asm/resume")
async def asm_resume():
    """Resume paused ASM."""
    from src.bot.crypto_main import telegram_app
    if not telegram_app or not telegram_app.bot_data.get("orchestrator"):
        raise HTTPException(503, "Crypto bot not initialized")

    orch = telegram_app.bot_data["orchestrator"]
    redis_client = telegram_app.bot_data["redis_client"]
    from src.data.bybit_client import BybitClient
    bybit = BybitClient()
    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, redis_client, bybit)

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
