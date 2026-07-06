"""Karsa Trading System - Crypto Telegram Bot (Separate Instance)
Separate FastAPI app + python-telegram-bot polling for crypto trading.
Shares src/ package with main orchestrator.
"""

import asyncio
from contextlib import asynccontextmanager
import redis.asyncio as redis

from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from src.config import settings
from src.bot.crypto_handlers import (
    start_cmd, dashboard_cmd, activity_cmd,
    portfolio_cmd, performance_cmd, control_cmd,
    mode_cmd, setmode_cmd, universe_cmd, refresh_universe_cmd,
    replay_cmd, events_cmd, button_callback,
    session_history_cmd, manage_profiles_cmd, open_positions_cmd,
)
from src.bot.aode_handlers import (
    cmd_discover, cmd_opportunity, cmd_narrative,
    cmd_watchlist, cmd_buckets, cmd_aode_research, cmd_aode_smartmoney,
)
from src.data.cache import CacheManager
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter
from src.agents.orchestrator import Orchestrator
from src.utils.logging import get_logger

logger = get_logger("crypto_bot")
telegram_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
    
    telegram_app = Application.builder().token(token).build()

    # Core 5 Commands Maps to the 5 dashboards
    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    telegram_app.add_handler(CommandHandler("activity", activity_cmd))
    telegram_app.add_handler(CommandHandler("portfolio", portfolio_cmd))
    telegram_app.add_handler(CommandHandler("performance", performance_cmd))
    telegram_app.add_handler(CommandHandler("control", control_cmd))
    telegram_app.add_handler(CommandHandler("mode", mode_cmd))
    telegram_app.add_handler(CommandHandler("setmode", setmode_cmd))
    telegram_app.add_handler(CommandHandler("universe", universe_cmd))
    telegram_app.add_handler(CommandHandler("refresh_universe", refresh_universe_cmd))
    telegram_app.add_handler(CommandHandler("replay", replay_cmd))
    telegram_app.add_handler(CommandHandler("events", events_cmd))

    # AODE Research Commands
    telegram_app.add_handler(CommandHandler("discover", cmd_discover))
    telegram_app.add_handler(CommandHandler("research", cmd_aode_research))
    telegram_app.add_handler(CommandHandler("opportunity", cmd_opportunity))
    telegram_app.add_handler(CommandHandler("narrative", cmd_narrative))
    telegram_app.add_handler(CommandHandler("smartmoney", cmd_aode_smartmoney))
    telegram_app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    telegram_app.add_handler(CommandHandler("buckets", cmd_buckets))
    
    # Unified Callback Handler
    telegram_app.add_handler(CallbackQueryHandler(button_callback))

    # Wire up orchestrator
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    cache = CacheManager(redis_client)
    mcp = MCPClient(cache)
    rl = RateLimiter(redis_client)
    orch = Orchestrator(mcp, cache, rl)
    
    # Wire risk profile + universe engine
    from src.risk.profile_manager import RiskProfileManager
    from src.advisory.crypto_universe import UniverseEngine
    profile_mgr = RiskProfileManager(redis_client)
    await profile_mgr.ensure_default()
    orch.profile_manager = profile_mgr
    bybit = mcp._get_bybit()
    orch.universe_engine = UniverseEngine(bybit, redis_client, profile_mgr)

    telegram_app.bot_data["orchestrator"] = orch
    telegram_app.bot_data["redis_client"] = redis_client

    await telegram_app.initialize()
    await telegram_app.start()

    # Wire Telegram event subscriber (Phase 2.2)
    try:
        from src.architecture.events import event_bus as _event_bus
        from src.architecture.events.subscribers import telegram_subscriber, configure_telegram_subscriber
        chat_id = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else 0
        if chat_id and telegram_app.bot:
            configure_telegram_subscriber(telegram_app.bot, chat_id)
            for evt in ["PositionOpened", "PositionReduced", "PositionClosed",
                        "TrailingActivated", "BreakEvenActivated", "StopLossTriggered",
                        "StopLossRecovered"]:
                _event_bus.subscribe(evt, telegram_subscriber)
            logger.info("telegram_event_subscriber_wired")
            from src.metrics.crypto_metrics import EVENT_BUS_ACTIVE
            EVENT_BUS_ACTIVE.set(1)
    except Exception as e:
        logger.warning("telegram_subscriber_setup_failed", error=str(e))

    # Wire Redis cross-process event delivery for Telegram bot
    try:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        from src.architecture.events.redis_bus import subscribe_redis_events
        async def _redis_event_handler(data: dict):
            from src.architecture.events.subscribers import telegram_subscriber
            from src.architecture.events.base import EventEnvelope
            env = EventEnvelope(
                event_type=data.get("event_type", ""),
                aggregate_id=data.get("aggregate_id", ""),
                aggregate_type=data.get("aggregate_type", ""),
                payload=data.get("payload", {}),
                publisher=data.get("publisher", ""),
            )
            await telegram_subscriber(env)
        await subscribe_redis_events(redis_client, _redis_event_handler)
        logger.info("redis_cross_process_events_subscribed")
    except Exception as e:
        logger.warning("redis_event_subscribe_failed", error=str(e))

    if not settings.TELEGRAM_WEBHOOK_URL:
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("crypto_bot_polling_mode")
    else:
        logger.info("crypto_bot_webhook_mode")

    yield

    try:
        if telegram_app.updater:
            await telegram_app.updater.stop()
    except Exception:
        pass
    await telegram_app.stop()
    await telegram_app.shutdown()
    await redis_client.aclose()
    await mcp.close()
    logger.info("crypto_bot_stopped")


app = FastAPI(title="Karsa Crypto Bot", lifespan=lifespan)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "crypto-bot"}

@app.get("/metrics")
async def metrics():
    """Expose Prometheus metrics for Grafana dashboard."""
    import src.metrics.crypto_metrics  # ensure registered
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/alert")
async def alertmanager_webhook(payload: dict):
    """Receive Alertmanager webhook and forward to Telegram."""
    alerts = payload.get("alerts", [])
    for alert in alerts:
        status = alert.get("status", "firing")
        severity = alert.get("labels", {}).get("severity", "unknown")
        summary = alert.get("annotations", {}).get("summary", "No summary")
        desc = alert.get("annotations", {}).get("description", "")

        icon = "🚨" if status == "firing" else "✅"
        prefix = "🔴 CRITICAL" if severity == "critical" else "🟡 WARNING"

        text = f"{icon} {prefix}\n<b>{summary}</b>"
        if desc:
            text += f"\n{desc}"

        try:
            chat_id = settings.TELEGRAM_CHAT_ID
            if chat_id and telegram_app:
                await telegram_app.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode="HTML"
                )
                logger.info("alert_forwarded", status=status, severity=severity)
        except Exception as e:
            logger.error("alert_forward_failed", error=str(e))

    return {"status": "ok", "processed": len(alerts)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8444)
