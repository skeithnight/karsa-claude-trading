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
    start_cmd, status_cmd, portfolio_cmd, scan_cmd, pnl_cmd,
    risk_cmd, kill_cmd, sellall_cmd, resume_cmd, activity_cmd,
    audit_agent_cmd, button_callback,
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
    if not token:
        logger.error("crypto_telegram_token_missing")
        yield
        return

    telegram_app = Application.builder().token(token).build()

    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(CommandHandler("status", status_cmd))
    telegram_app.add_handler(CommandHandler("portfolio", portfolio_cmd))
    telegram_app.add_handler(CommandHandler("scan", scan_cmd))
    telegram_app.add_handler(CommandHandler("pnl", pnl_cmd))
    telegram_app.add_handler(CommandHandler("risk", risk_cmd))
    telegram_app.add_handler(CommandHandler("kill", kill_cmd))
    telegram_app.add_handler(CommandHandler("sellall", sellall_cmd))
    telegram_app.add_handler(CommandHandler("resume", resume_cmd))
    telegram_app.add_handler(CommandHandler("activity", activity_cmd))
    telegram_app.add_handler(CommandHandler("audit_agent", audit_agent_cmd))
    telegram_app.add_handler(CallbackQueryHandler(button_callback))

    # Wire up orchestrator
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    cache = CacheManager(redis_client)
    mcp = MCPClient(cache)
    rl = RateLimiter(redis_client)
    orch = Orchestrator(mcp, cache, rl)
    telegram_app.bot_data["orchestrator"] = orch

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
