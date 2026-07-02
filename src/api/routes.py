"""Karsa Trading System - REST API Routes for Risk Profile & Universe

Mounted on the orchestrator's FastAPI app (port 8000).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["risk-profile", "universe"])


# --- Risk Profile Endpoints ---

class ProfileUpdateRequest(BaseModel):
    profile: str
    reason: str = ""


@router.get("/risk-profile")
async def get_risk_profile():
    """Get the currently active risk profile."""
    from src.main import karsa_app
    if not karsa_app or not karsa_app.profile_manager:
        raise HTTPException(503, "Profile manager not initialized")

    p = await karsa_app.profile_manager.get_active_profile()
    return {
        "name": p.name,
        "emoji": p.emoji,
        "min_confidence": p.min_confidence,
        "max_position_size_pct": p.max_position_size_pct,
        "stop_loss_atr_mult": p.stop_loss_atr_mult,
        "take_profit_atr_mult": p.take_profit_atr_mult,
        "max_open_positions": p.max_open_positions,
        "max_daily_trades": p.max_daily_trades,
        "max_correlation": p.max_correlation,
        "min_volume_24h_usd": p.min_volume_24h_usd,
        "regime_veto_strictness": p.regime_veto_strictness,
        "size_multiplier": p.size_multiplier,
    }


@router.put("/risk-profile")
async def update_risk_profile(req: ProfileUpdateRequest):
    """Switch the active risk profile."""
    from src.main import karsa_app
    from src.risk.profile_manager import RiskProfile
    if not karsa_app or not karsa_app.profile_manager:
        raise HTTPException(503, "Profile manager not initialized")

    try:
        profile = RiskProfile(req.profile.lower().replace("-", "_"))
    except ValueError:
        raise HTTPException(400, f"Invalid profile: {req.profile}")

    ok = await karsa_app.profile_manager.set_profile(profile, "api", req.reason)
    if not ok:
        raise HTTPException(429, "Cooldown active — wait 5 minutes")

    p = await karsa_app.profile_manager.get_active_profile()
    return {"status": "ok", "active_profile": p.name}


@router.get("/risk-profile/history")
async def get_risk_profile_history(limit: int = 50):
    """Get recent risk profile change history."""
    from src.main import karsa_app
    if not karsa_app or not karsa_app.profile_manager:
        raise HTTPException(503, "Profile manager not initialized")

    return await karsa_app.profile_manager.get_audit_log(limit=min(limit, 100))


# --- Universe Endpoints ---

@router.get("/universe")
async def get_universe():
    """Get the current dynamic crypto universe."""
    from src.main import karsa_app
    if not karsa_app or not karsa_app.universe_engine:
        raise HTTPException(503, "Universe engine not initialized")

    universe = await karsa_app.universe_engine.get_current()
    return {"count": len(universe), "coins": universe}


@router.post("/universe/refresh")
async def refresh_universe():
    """Force regenerate the dynamic universe."""
    from src.main import karsa_app
    if not karsa_app or not karsa_app.universe_engine:
        raise HTTPException(503, "Universe engine not initialized")

    universe = await karsa_app.universe_engine.generate()
    return {"status": "ok", "count": len(universe), "coins": universe}


@router.get("/universe/scores")
async def get_universe_scores():
    """Get universe with scoring details."""
    from src.main import karsa_app
    if not karsa_app or not karsa_app.universe_engine:
        raise HTTPException(503, "Universe engine not initialized")

    ranked = await karsa_app.universe_engine.get_universe_with_scores()
    return {"count": len(ranked), "coins": ranked}
