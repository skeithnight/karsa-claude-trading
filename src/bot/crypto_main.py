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
    portfolio_cmd, performance_cmd, control_cmd, settings_cmd,
    mode_cmd, setmode_cmd, universe_cmd, refresh_universe_cmd,
    replay_cmd, events_cmd, button_callback,
    session_history_cmd, manage_profiles_cmd, open_positions_cmd,
    clear_halt_cmd, view_positions_detail_cmd, trade_history_cmd,
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

    # Init DB engine (required for trade history, health checks, reconciliation)
    from src.models.database import init_db, close_db
    try:
        await init_db()
        logger.info("db_engine_initialized")
    except Exception as e:
        logger.error("db_init_failed", error=str(e))

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
    telegram_app.add_handler(CommandHandler("clear_halt", clear_halt_cmd))
    telegram_app.add_handler(CommandHandler("settings", settings_cmd))

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
    # Expose on app.state for API routes (avoids __main__ vs module import issue)
    app.state.orchestrator = orch
    app.state.redis_client = redis_client
    app.state.telegram_app = telegram_app

    # Connection health monitor — sends Telegram alerts on failures
    async def _connection_health_loop():
        """Check critical connections every 60s, alert on failures.

        Classifies errors as FATAL (config/code bug) vs TRANSIENT (network blip).
        Fatal errors show "manual fix required" — no misleading "retry in 60s".
        """
        import asyncio
        from datetime import datetime
        from src.utils.error_classification import classify_error, ErrorSeverity

        chat_id = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else 0
        if not chat_id:
            return

        # Track last alert time to avoid spam (min 5 min between same alerts)
        last_alerts = {}

        while True:
            await asyncio.sleep(60)
            now = datetime.now()
            fatal_checks = []   # (service, description)
            transient_checks = []  # (service, description)

            # 1. Redis check
            try:
                await redis_client.ping()
            except Exception as e:
                sev = classify_error(e)
                entry = ("🔴 Redis", str(e)[:50])
                if sev == ErrorSeverity.FATAL:
                    fatal_checks.append(entry)
                else:
                    transient_checks.append(entry)

            # 2. Bybit check — reuse shared client, thread-safe with timeout
            try:
                bybit = orch.mcp._get_bybit()
                await bybit._throttle()
                resp = await asyncio.wait_for(
                    asyncio.to_thread(
                        bybit._safe_pybit_call,
                        bybit._http_client.get_server_time,
                    ),
                    timeout=10.0,
                )
                if resp.get("retCode") != 0:
                    sev = classify_error(Exception(resp.get("retMsg", "")))
                    entry = ("🔴 Bybit API", resp.get("retMsg", "unknown error"))
                    if sev == ErrorSeverity.FATAL:
                        fatal_checks.append(entry)
                    else:
                        transient_checks.append(entry)
                # pybit HTTP uses requests (sync) — no async cleanup needed
            except Exception as e:
                sev = classify_error(e)
                entry = ("🔴 Bybit API", str(e)[:50])
                if sev == ErrorSeverity.FATAL:
                    fatal_checks.append(entry)
                else:
                    transient_checks.append(entry)

            # 3. 9Router/LLM check — any HTTP response = reachable; only flag connection errors
            try:
                import httpx
                headers = {}
                if settings.NROUTER_AUTH_TOKEN:
                    headers["Authorization"] = f"Bearer {settings.NROUTER_AUTH_TOKEN}"
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{settings.NROUTER_BASE_URL}/health", headers=headers)
                    # 200/404 both mean9Router is reachable (404 = no health endpoint, that's OK)
                    if resp.status_code not in (200, 404):
                        sev = classify_error(Exception(f"HTTP {resp.status_code}"), status_code=resp.status_code)
                        entry = ("🔴 9Router/LLM", f"status {resp.status_code}")
                        if sev == ErrorSeverity.FATAL:
                            fatal_checks.append(entry)
                        else:
                            transient_checks.append(entry)
            except Exception as e:
                sev = classify_error(e)
                entry = ("🔴 9Router/LLM", str(e)[:50])
                if sev == ErrorSeverity.FATAL:
                    fatal_checks.append(entry)
                else:
                    transient_checks.append(entry)

            # 4. Postgres check
            try:
                from src.models.database import async_session
                from sqlalchemy import text
                async with async_session() as session:
                    await session.execute(text("SELECT 1"))
            except Exception as e:
                sev = classify_error(e)
                entry = ("🔴 Postgres", str(e)[:50])
                if sev == ErrorSeverity.FATAL:
                    fatal_checks.append(entry)
                else:
                    transient_checks.append(entry)

            all_checks = fatal_checks + transient_checks

            # Send alert if any failures
            if all_checks:
                # Throttle: min 5 min between same service alerts
                alert_key = "|".join(sorted(c[0] for c in all_checks))
                if alert_key in last_alerts:
                    if (now - last_alerts[alert_key]).seconds < 300:
                        continue

                lines = [f"🚨 <b>Connection Alert</b> — {now.strftime('%H:%M')}\n"]

                if fatal_checks:
                    lines.append("🛑 <b>FATAL — Manual Fix Required:</b>")
                    for service, err in fatal_checks:
                        lines.append(f"  {service}: {err}")
                    lines.append("")

                if transient_checks:
                    lines.append("⚠️ <b>Transient — Auto-Retrying:</b>")
                    for service, err in transient_checks:
                        lines.append(f"  {service}: {err}")
                    lines.append("")
                    lines.append("⏳ Will retry in 60s...")
                else:
                    lines.append("🛑 Bot cannot auto-recover. Check .env and restart.")

                try:
                    from src.notifications.router import NotificationRouter, NotificationCategory
                    notifier = NotificationRouter(telegram_app.bot, chat_id)
                    await notifier.send(
                        "\n".join(lines),
                        NotificationCategory.INFRASTRUCTURE,
                    )
                    last_alerts[alert_key] = now
                except Exception:
                    pass

            # Send recovery alert if previously down and now all ok
            elif last_alerts:
                try:
                    from src.notifications.router import NotificationRouter, NotificationCategory
                    notifier = NotificationRouter(telegram_app.bot, chat_id)
                    await notifier.send(
                        f"✅ <b>All connections restored</b> — {now.strftime('%H:%M')}",
                        NotificationCategory.INFRASTRUCTURE,
                    )
                    last_alerts.clear()
                except Exception:
                    pass

    asyncio.create_task(_connection_health_loop())
    logger.info("connection_health_monitor_started")

    # Wire high-frequency risk monitor (P0 — decoupled from scan loop)
    try:
        from src.risk.risk_monitor import HighFrequencyRiskMonitor
        chat_id = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else 0
        risk_monitor = HighFrequencyRiskMonitor(orch, redis_client, bybit, chat_id)
        telegram_app.bot_data["risk_monitor"] = risk_monitor
        await risk_monitor.start()
        logger.info("risk_monitor_wired")
    except Exception as e:
        logger.warning("risk_monitor_setup_failed", error=str(e))

    # Service watchdog — monitors health with graduated recovery
    try:
        from src.monitoring.watchdog import ServiceWatchdog
        chat_id = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else 0
        watchdog = ServiceWatchdog(redis_client, "bot", chat_id)
        watchdog.set_telegram_bot(telegram_app.bot)
        await watchdog.start()
        logger.info("bot_watchdog_started")
    except Exception as e:
        logger.warning("bot_watchdog_setup_failed", error=str(e))

    # Startup reconciliation — sync Bybit positions with DB/Redis
    try:
        from src.agents.autonomous_session import AutonomousSessionManager
        asm = AutonomousSessionManager(orch, redis_client, bybit)
        reconcile_msg = await asm.reconcile_state()
        if reconcile_msg and chat_id:
            from src.notifications.router import NotificationRouter, NotificationCategory
            notifier = NotificationRouter(telegram_app.bot, chat_id)
            await notifier.send(reconcile_msg, NotificationCategory.MANUAL_COMMAND)
        # If session is active, resume the loop
        if await asm.is_active():
            asyncio.create_task(asm._run_loop(chat_id))
            logger.info("asm_loop_resumed_on_startup")
    except Exception as e:
        logger.warning("reconciliation_setup_failed", error=str(e))

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
    await close_db()
    logger.info("crypto_bot_stopped")


app = FastAPI(title="Karsa Crypto Bot", lifespan=lifespan)

from src.api.crypto_control import router as crypto_control_router
app.include_router(crypto_control_router)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "crypto-bot"}

@app.get("/debug/telegram_app")
async def debug_telegram_app():
    return {
        "telegram_app_is_none": telegram_app is None,
        "telegram_app_type": str(type(telegram_app)) if telegram_app else None,
        "has_bot_data": hasattr(telegram_app, "bot_data") if telegram_app else False,
        "bot_data_keys": list(telegram_app.bot_data.keys()) if telegram_app and hasattr(telegram_app, "bot_data") else [],
    }

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
                from src.notifications.router import NotificationRouter, NotificationCategory
                notifier = NotificationRouter(telegram_app.bot, chat_id)
                await notifier.send(text, NotificationCategory.INFRASTRUCTURE, force=True)
                logger.info("alert_forwarded", status=status, severity=severity)
        except Exception as e:
            logger.error("alert_forward_failed", error=str(e))

    return {"status": "ok", "processed": len(alerts)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8444)
