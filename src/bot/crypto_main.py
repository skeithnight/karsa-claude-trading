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
    button_callback
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
    orch.profile_manager = profile_mgr
    bybit = mcp._get_bybit()
    orch.universe_engine = UniverseEngine(bybit, redis_client, profile_mgr)

    telegram_app.bot_data["orchestrator"] = orch
    telegram_app.bot_data["redis_client"] = redis_client

    await telegram_app.initialize()
    await telegram_app.start()

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8444)
