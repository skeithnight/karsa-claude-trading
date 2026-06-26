"""Karsa Trading System - Telegram Bot Server

Supports two modes:
- Polling (default): No domain needed. Bot pulls updates from Telegram.
- Webhook: Requires a public domain + HTTPS. Faster, lower latency.
"""

from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from src.config import settings
from src.bot.handlers import start_cmd, status_cmd, scan_cmd, portfolio_cmd, trades_cmd, handle_approval_callback
from src.bot.approval import ApprovalManager
from src.data.cache import CacheManager
from src.execution.idx_broker import IDXBroker
from src.execution.us_broker import USBroker
from src.models.database import async_session
from src.utils.logging import get_logger

logger = get_logger("telegram_bot")

telegram_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    telegram_app = Application.builder().token(settings.TELEGRAM_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(CommandHandler("status", status_cmd))
    telegram_app.add_handler(CommandHandler("scan", scan_cmd))
    telegram_app.add_handler(CommandHandler("portfolio", portfolio_cmd))
    telegram_app.add_handler(CommandHandler("trades", trades_cmd))
    telegram_app.add_handler(CallbackQueryHandler(handle_approval_callback))

    # Wire up approval manager, orchestrator, and brokers into bot_data
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    cache = CacheManager(redis_client)

    from src.data.mcp_client import MCPClient
    from src.data.idx_adapter import IDXDataAdapter
    from src.utils.rate_limit import RateLimiter
    from src.agents.orchestrator import Orchestrator
    mcp = MCPClient(cache)
    idx_adapter = IDXDataAdapter(cache)
    rl = RateLimiter(redis_client)
    orch = Orchestrator(mcp, idx_adapter, cache, rl)

    approval_mgr = ApprovalManager(cache, async_session)
    brokers = {"IDX": IDXBroker(), "US": USBroker()}

    telegram_app.bot_data["approval_manager"] = approval_mgr
    telegram_app.bot_data["orchestrator"] = orch
    telegram_app.bot_data["brokers"] = brokers

    await telegram_app.initialize()
    await telegram_app.start()

    # Start polling in the background (non-blocking)
    if not settings.TELEGRAM_WEBHOOK_URL:
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram_bot_polling_mode")
    else:
        logger.info("telegram_bot_webhook_mode", url=settings.TELEGRAM_WEBHOOK_URL)

    yield

    try:
        if telegram_app.updater:
            await telegram_app.updater.stop()
    except Exception:
        pass
    await telegram_app.stop()
    await telegram_app.shutdown()
    for b in brokers.values():
        await b.close()
    await redis_client.close()
    logger.info("telegram_bot_stopped")


app = FastAPI(title="Karsa Telegram Bot", lifespan=lifespan)


# --- Webhook endpoint (only used when TELEGRAM_WEBHOOK_URL is set) ---

async def verify_telegram_secret(x_telegram_bot_api_secret_token: str = Header(None)):
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        return
    if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
        logger.warning("invalid_webhook_secret")
        raise HTTPException(status_code=403, detail="Invalid secret token")


@app.post("/webhook", dependencies=[Depends(verify_telegram_secret)])
async def telegram_webhook(request: Request):
    global telegram_app
    if not telegram_app:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)

        chat_id = None
        if update.message:
            chat_id = update.message.chat_id
        elif update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat_id

        if chat_id and str(chat_id) != settings.TELEGRAM_CHAT_ID:
            logger.warning("unauthorized_chat_id", chat_id=chat_id)
            return {"status": "ignored"}

        await telegram_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error("webhook_processing_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8443)
