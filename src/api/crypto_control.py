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
    from src.agents.autonomous_session import AutonomousSessionManager
    bybit = orch.mcp._get_bybit()  # Reuse shared client — no new connection pool
    return AutonomousSessionManager(orch, redis_client, bybit)


# --- ASM Endpoints ---

@router.get("/asm/status")
async def asm_status(request: Request):
    """Get ASM state, config, equity, and emergency status."""
    from src.risk import emergency
    import json

    # Use shared Redis client from app.state instead of creating a new pool per request
    _, r = _get_app_state(request)
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


# --- Watchdog Endpoints ---

@router.get("/watchdog")
async def watchdog_status(request: Request):
    """Get watchdog health status with score, diagnostics, and subsystems."""
    import time
    import json
    _, redis_client = _get_app_state(request)
    now = time.time()

    # Health score
    health_score = None
    try:
        score_raw = await redis_client.get("karsa:watchdog:health_score")
        if score_raw:
            health_score = float(score_raw)
    except Exception:
        pass

    # Sentinel (event loop health)
    sentinel_lag = None
    try:
        sentinel_raw = await redis_client.get("karsa:watchdog:sentinel")
        if sentinel_raw:
            sentinel_lag = round(now - float(sentinel_raw), 1)
    except Exception:
        pass

    # Subsystem heartbeats
    subsystems = {}
    try:
        keys = await redis_client.keys("karsa:watchdog:*")
        for key in keys:
            # Skip non-heartbeat keys
            if any(skip in key for skip in [":restart", ":diagnostic", ":health_score", ":sentinel"]):
                continue
            val = await redis_client.get(key)
            parts = key.split(":")
            if len(parts) >= 4:
                service = parts[2]
                subsystem = parts[3]
                age = now - float(val) if val else 999
                subsystems[f"{service}:{subsystem}"] = {
                    "last_heartbeat": float(val) if val else None,
                    "age_seconds": round(age, 1),
                    "healthy": age < 180,
                }
    except Exception:
        pass

    # Last restart reason
    restart_reason = None
    try:
        raw = await redis_client.get("karsa:watchdog:restart_reason")
        if raw:
            try:
                restart_reason = json.loads(raw)
            except Exception:
                restart_reason = raw
    except Exception:
        pass

    # Diagnostic snapshot (from last hard restart)
    diagnostic = None
    try:
        raw = await redis_client.get("karsa:watchdog:diagnostic")
        if raw:
            try:
                diagnostic = json.loads(raw)
            except Exception:
                diagnostic = raw
    except Exception:
        pass

    # Overall status
    status = "healthy"
    if health_score is not None:
        if health_score < 40:
            status = "critical"
        elif health_score < 70:
            status = "degraded"

    return {
        "status": status,
        "health_score": health_score,
        "sentinel_lag_sec": sentinel_lag,
        "subsystems": subsystems,
        "restart_reason": restart_reason,
        "diagnostic": diagnostic,
    }


@router.get("/watchdog/diagnostic")
async def watchdog_diagnostic(request: Request):
    """Get last diagnostic snapshot from hard restart."""
    import json
    _, redis_client = _get_app_state(request)
    try:
        raw = await redis_client.get("karsa:watchdog:diagnostic")
        if raw:
            return json.loads(raw)
        return {"status": "no diagnostic available"}
    except Exception as e:
        raise HTTPException(500, f"Failed to read diagnostic: {e}")
