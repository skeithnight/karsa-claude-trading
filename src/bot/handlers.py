"""Karsa Trading System - Telegram Bot Command Handlers (Composable Format)"""

from decimal import Decimal, InvalidOperation
from src.utils.validation import validate_ticker, validate_market, sanitize_for_prompt
from src.utils.format import HTML, bold, italic, code, pre, fmt, join
from telegram import Update
from telegram.ext import ContextTypes
import httpx

from src.config import settings, LLM_BASE_URL
from src.utils.logging import get_logger
from src.risk import emergency
from src.bot._approval import send_signal_alert, handle_approval

logger = get_logger("telegram_handlers")


def parse_decimal(raw: str) -> Decimal:
    """Parse number string handling both comma and dot as decimal separator.

    Rules:
    - Contains both . and , -> last one is decimal separator (1,234.56 or 1.234,56)
    - Contains only , -> decimal separator (0,006421695)
    - Contains only . -> decimal separator (0.006421695)
    - Neither -> integer

    Raises ValueError on invalid input.
    """
    raw = raw.strip()
    if not raw or len(raw) > 30:
        raise ValueError(f"Invalid number: {raw!r}")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid number: {raw!r}")


def _is_authorized(update: Update) -> bool:
    """Check if message is from authorized chat. Fail closed if TELEGRAM_CHAT_ID not set."""
    if not settings.TELEGRAM_CHAT_ID:
        logger.error("telegram_chat_id_not_configured")
        return False
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if chat_id != str(settings.TELEGRAM_CHAT_ID):
        logger.warning("unauthorized_chat")
        return False
    return True


async def _reply(update: Update, content, add_timestamp: bool = True, **kwargs):
    """Reply with auto-detection of formatted content."""
    if add_timestamp:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        content = fmt(italic(ts), "\n", content)

    if isinstance(content, HTML) and "parse_mode" not in kwargs:
        kwargs["parse_mode"] = "HTML"

    text = str(content)

    if update.callback_query:
        return await update.callback_query.message.edit_text(text, **kwargs)
    elif update.message:
        return await update.message.reply_text(text, **kwargs)
    return None


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await _reply(update, fmt(
        bold("🤖 Karsa Advisory Desk"), "\n\n",
        "AI-driven trading desk for IDX, US, and ETF markets.\n",
        "Use ", code("/guide"), " for full walkthrough.\n\n",
        bold("Quick Commands:"), "\n",
        code("/portfolio"), " — View holdings & cash\n",
        code("/briefing"), " — Morning dashboard + IDX intel\n",
        code("/idx"), " — IDX Intelligence dashboard\n",
        code("/scan <market> <ticker>"), " — Scan a stock\n",
        code("/analyze"), " — AI portfolio review\n",
        code("/regime"), " — Market state\n",
        code("/guide"), " — Full 101 walkthrough\n",
    ))


async def guide_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /guide — full walkthrough of Karsa."""
    if not _is_authorized(update):
        return

    from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

    msg = fmt(
        bold("📖 KARSA 101 — Your AI Trading Desk"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",

        bold("🤖 What is Karsa?"), "\n",
        "Karsa is an AI-powered advisory desk that scans markets, generates signals, ",
        "and tracks a shadow paper portfolio — all through Telegram.\n\n",
        "Karsa does NOT trade your real money. It provides analysis and paper-trades ",
        "to help you make informed decisions. You approve or reject every signal.\n\n",

        bold("📋 SUPPORTED MARKETS"), "\n",
        "• ", bold("IDX"), " — Indonesian stocks (BBCA, BBRI, BMRI, TLKM ...)\n",
        "• ", bold("US"), " — US equities (NVDA, AAPL, MSFT, GOOGL ...)\n",
        "• ", bold("ETF"), " — Global ETFs (SPY, QQQ, GLD, TLT ...)\n\n",

        bold("🧠 IDX INTELLIGENCE"), "\n",
        "Composite market regime scoring (-100 to +100) combining:\n",
        "• Breadth (30%) — advance/decline ratio\n",
        "• Sector Rotation (25%) — sector performance flow\n",
        "• Foreign Flow (20%) — volume-based proxy\n",
        "• Price Structure (25%) — IHSG vs SMA20/200\n\n",
        "Composite gates IDX scans:\n",
        "• Score ≤-50 → IDX scan skipped (bear regime)\n",
        "• Score ≤-20 → Caution, reduced position sizing\n\n",

        bold("🔄 HOW IT WORKS"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",

        bold("Step 1: Set Up Your Portfolio"), "\n",
        "  ", code("/add IDX BBCA 500 8500"), " — add 500 BBCA @ 8,500\n",
        "  ", code("/add US NVDA 10 120.50"), " — add 10 NVDA @ $120.50\n",
        "  ", code("/add cash IDR 50000000"), " — set IDR cash\n\n",

        bold("Step 2: Start Your Day"), "\n",
        "  ", code("/briefing"), " — Morning dashboard & regime check\n",
        "  ", code("/scan portfolio"), " — scan ALL your holdings at once\n\n",

        bold("Step 3: Review Signals"), "\n",
        "When Karsa finds an opportunity (confidence >= 60/100), it sends an alert. ",
        "If approved -> paper trade executed. If rejected -> discarded.\n\n",

        bold("Step 4: Track Performance"), "\n",
        "  ", code("/portfolio"), " — view all holdings, avg cost, unrealized P&L\n",
        "  ", code("/pnl"), " — shadow portfolio: open P&L, win rate, realized P&L\n",
        "  ", code("/trades"), " — paper trading history\n",
        "  ", code("/audit <ticker>"), " — see AI reasoning behind a signal\n\n",

        bold("Step 5: Manage Positions"), "\n",
        "  ", code("/edit IDX BBCA qty 600"), " — update quantity\n",
        "  ", code("/remove IDX BBCA"), " — remove position\n\n",

        bold("📊 COMMAND REFERENCE"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",

        bold("Portfolio:"), "\n",
        "  ", code("/portfolio"), " — view all holdings & cash\n",
        "  ", code("/add"), " — add position or cash\n",
        "  ", code("/remove"), " — remove position\n",
        "  ", code("/edit"), " — edit position or cash\n\n",

        bold("Analysis:"), "\n",
        "  ", code("/scan <market> <ticker>"), " — quick scan\n",
        "  ", code("/scan portfolio"), " — scan all holdings\n",
        "  ", code("/analyze"), " — full portfolio AI review\n",
        "  ", code("/audit <ticker>"), " — signal audit trail\n\n",

        bold("CIO Dashboard:"), "\n",
        "  ", code("/briefing"), " — morning dashboard + IDX intel\n",
        "  ", code("/idx"), " — IDX Intelligence dashboard\n",
        "  ", code("/regime"), " — market regime (US + IDX)\n",
        "  ", code("/pnl"), " — shadow portfolio P&L\n",
        "  ", code("/trades"), " — paper trade history\n\n",

        bold("⚠️ IMPORTANT NOTES"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        "• Karsa is an ", bold("advisory system"), ", not a broker\n",
        "• All trades are ", bold("paper trades"), " (shadow portfolio)\n",
        "• Kill switch triggers at -1.5% daily P&L\n",
    )

    keyboard = build_nav_keyboard([
        [("💼 Portfolio", "cmd_portfolio"), ("☀️ Briefing", "cmd_briefing")],
        [("🌡️ Regime", "cmd_regime"), ("📊 P&L", "cmd_pnl")],
    ])
    await send_long_message(update, str(msg), reply_markup=keyboard)


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan <market> <ticker> or /scan portfolio."""
    if not _is_authorized(update):
        return
    if not context.args:
        await _reply(update, fmt(
            "⚠️ Usage:\n",
            code("/scan <market> <ticker>"), " — scan one ticker\n",
            code("/scan portfolio"), " — scan all holdings"
        ))
        return

    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await _reply(update, "⚠️ System error: Orchestrator not connected.")
        return

    # /scan portfolio
    if context.args[0].upper() == "PORTFOLIO":
        msg = await _reply(update, bold("🔍 Scanning entire portfolio..."))
        try:
            from src.models.database import async_session
            from src.models.tables import PortfolioState
            from sqlalchemy import select
            from collections import defaultdict
            from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard

            async with async_session() as session:
                result = await session.execute(select(PortfolioState).order_by(PortfolioState.market, PortfolioState.ticker))
                positions = result.scalars().all()

            if not positions:
                await msg.edit_text("📭 No positions to scan. Use /add first.")
                return

            port_list = [{"market": p.market, "ticker": p.ticker} for p in positions]
            scan_result = await orchestrator.scan_portfolio(port_list)

            lines = [bold(f"🔍 PORTFOLIO SCAN — {len(port_list)} tickers")]
            by_market = defaultdict(list)
            for r in scan_result.get("results", []):
                by_market[r.get("market", "UNKNOWN")].append(r)

            rec_emoji = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "⚪️"}

            for market in ["IDX", "US", "ETF"]:
                if market not in by_market:
                    continue
                headers = ["Ticker", "Strategy", "Conf", "Dir", "Reasoning"]
                rows = []
                for r in sorted(by_market[market], key=lambda x: x.get("confidence_score", 0), reverse=True):
                    conf = r.get("confidence_score", 0)
                    direction = r.get("direction", "N/A")
                    d_emoji = rec_emoji.get(direction, "⚪️")
                    reasoning = (r.get("reasoning", "") or "")[:60]
                    rows.append([r.get("ticker", "?"), r.get("strategy", "?")[:12], f"{conf}/100", f"{d_emoji} {direction}", reasoning])
                table = format_pre_table(headers, rows, align_right=[2])
                lines.append(fmt("\n", bold(f"📈 {market} MARKET"), "\n", pre(table)))

            errors = scan_result.get("errors", [])
            if errors:
                err_lines = [f"  • {e['ticker']}: {e['error'][:40]}" for e in errors[:5]]
                lines.append(fmt("\n⚠️ ", bold(f"{len(errors)} failed:"), "\n", join(err_lines)))

            keyboard = build_nav_keyboard([
                [("🧠 Analyze", "cmd_analyze"), ("📊 P&L", "cmd_pnl")],
                [("☀️ Briefing", "cmd_briefing"), ("💼 Portfolio", "cmd_portfolio")],
            ])
            await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)

        except Exception as e:
            logger.error("scan_portfolio_failed", error=str(e), exc_info=True)
            await msg.edit_text("❌ Scan failed. Check logs.")
        return

    # /scan <market> <ticker>
    if len(context.args) < 2:
        await _reply(update, fmt("⚠️ Usage: ", code("/scan IDX BBCA")))
        return

    market, ticker = context.args[0].upper(), context.args[1].upper()
    if not validate_market(market) or not validate_ticker(ticker):
        await _reply(update, "⚠️ Invalid market or ticker format.")
        return

    msg = await _reply(update, fmt("🔍 Scanning ", bold(ticker), " (", market, ")..."))

    try:
        result = await orchestrator.scan_single(market, ticker)
        if result.get("error"):
            await msg.edit_text(str(fmt("❌ Scan failed: ", result['error'])))
            return

        text = fmt(
            bold(f"ℹ️ Scan: {ticker} ({market})"), "\n",
            "Strategy: ", result.get('strategy', 'Unknown'), "\n",
            "Confidence: ", str(result.get('confidence_score', 0)), "/100\n",
            "Direction: ", result.get('direction', 'N/A'), "\n\n",
            bold("📝 Reasoning:"), "\n",
            result.get('reasoning', 'No reasoning provided.')
        )
        await msg.edit_text(str(text), parse_mode="HTML")

        # Send approval alert if confidence >= 60
        conf = result.get("confidence_score", 0)
        if conf >= 60 and not result.get("error") and not result.get("validation_issues"):
            try:
                from src.models.database import async_session
                from src.models.tables import Signal
                from sqlalchemy import select, desc
                async with async_session() as s:
                    sig = await s.execute(
                        select(Signal).where(Signal.ticker == ticker, Signal.market == market)
                        .order_by(desc(Signal.created_at)).limit(1)
                    )
                    signal = sig.scalar_one_or_none()
                    if signal and signal.status == "PENDING":
                        await send_signal_alert(msg, result, str(signal.id))
            except Exception as alert_err:
                logger.error("approval_alert_failed", error=str(alert_err))

    except Exception as e:
        logger.error("scan_cmd_failed", error=str(e), exc_info=True)
        await msg.edit_text("❌ Scan failed. Check logs.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    # Check DB
    db_ok = False
    try:
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    # Check Redis
    redis_ok = False
    try:
        orch = context.bot_data.get("orchestrator")
        redis_ok = await orch.cache.ping() if orch else False
    except Exception:
        pass

    # Check 9Router
    router_ok = False
    router_url = LLM_BASE_URL
    if not router_url:
        router_status = "⚪️ 9Router (Not Configured)"
    else:
        try:
            async with httpx.AsyncClient(timeout=3.0, verify=False) as client:
                await client.get(f"{router_url}/v1/models")
                router_ok = True
        except Exception:
            pass
        router_status = f"{'🟢' if router_ok else '🔴'} 9Router ({router_url})"

    # Check Scheduler
    scheduler_status = "⚪️ Scheduler (Unknown)"
    scheduler_error = ""
    jobs_info = []
    try:
        orchestrator_url = "http://karsa-orchestrator:8000"
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{orchestrator_url}/health/scheduler")
            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    scheduler_status = "🔴 Scheduler (Error)"
                    scheduler_error = data["error"]
                else:
                    scheduler_status = f"🟢 Scheduler ({data['status'].title()})"
                    for job in data.get("jobs", []):
                        next_run = job.get("next_run", "N/A")
                        if next_run and next_run != "N/A":
                            next_run = next_run.split("T")[1][:5]
                        jobs_info.append(f"  • {job['name']}: next at {next_run}")
            else:
                scheduler_status = "🔴 Scheduler (Unreachable)"
                scheduler_error = f"HTTP {resp.status_code}"
    except Exception as e:
        scheduler_error = str(e)[:100]

    # Check Kill Switch
    kill_switch_status = "🟢 Kill Switch (Inactive)"
    try:
        stop_active = await emergency.is_active()
        if stop_active:
            stop_info = await emergency.get_status()
            reason = stop_info.get("reason", "Unknown") if stop_info else "Unknown"
            kill_switch_status = fmt("🔴 Kill Switch (ACTIVE)\n", italic(f"Reason: {reason}"))
    except Exception:
        pass

    lines = [
        bold("📊 System Status"), "\n━━━━━━━━━━━━━━━━\n",
        f"{'🟢' if db_ok else '🔴'} PostgreSQL\n",
        f"{'🟢' if redis_ok else '🔴'} Redis\n",
        router_status, "\n",
        "🟢 Orchestrator\n\n",
        bold("Scheduler & Automation:"), "\n",
        scheduler_status, "\n",
        kill_switch_status,
    ]

    if scheduler_error:
        lines.append(italic(f"⚠️ {scheduler_error}"))

    if jobs_info:
        lines.extend(["\n", bold("Scheduled Jobs:"), "\n", join(jobs_info[:8])])

    lines.append("\n━━━━━━━━━━━━━━━━")
    await _reply(update, fmt(*lines))


async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View full portfolio & cash."""
    if not _is_authorized(update):
        return
    from src.models.database import async_session
    from src.models.tables import PortfolioState, CashBalance
    from sqlalchemy import select
    from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard
    from itertools import groupby

    try:
        async with async_session() as session:
            port_result = await session.execute(select(PortfolioState).order_by(PortfolioState.market, PortfolioState.ticker))
            positions = list(port_result.scalars().all())
            cash_result = await session.execute(select(CashBalance))
            cash_balances = cash_result.scalars().all()

        orchestrator = context.bot_data.get("orchestrator")
        stale = [p for p in positions if not p.current_price]
        if stale and orchestrator:
            async with async_session() as session:
                for p in stale:
                    try:
                        quote = await orchestrator.mcp.get_quote(p.ticker, p.market)
                        if quote and not quote.get("error") and quote.get("price"):
                            p.current_price = quote["price"]
                            if p.avg_cost:
                                p.unrealized_pnl = (p.current_price - p.avg_cost) * p.quantity
                            await session.merge(p)
                    except Exception:
                        pass
                await session.commit()

    except Exception as e:
        logger.error("portfolio_db_error", error=str(e), exc_info=True)
        await _reply(update, "❌ Database error. Check logs.")
        return

    cash_str = " | ".join([f"{c.balance:,.2f} {c.currency}" for c in cash_balances]) or "$0.00"

    lines = [
        bold("💼 PORTFOLIO OVERVIEW"), "\n",
        "💵 ", bold("Cash:"), " ", cash_str,
    ]

    if not positions:
        lines.extend([
            "\n\n", italic("📭 No positions open."), "\n",
            italic("💡 Use "), code("/add IDX BBCA 500 8500"), italic(" to add.")
        ])
    else:
        for market, market_positions in groupby(positions, key=lambda p: p.market):
            headers = ["Ticker", "Qty", "Avg Cost", "Curr Price", "Unrealized P&L"]
            rows = []
            for p in market_positions:
                if market == "IDX":
                    qty_str = f"{int(p.quantity):,}"
                    avg_str = f"{p.avg_cost:,.0f}"
                    curr_str = f"{p.current_price:,.0f}" if p.current_price else "N/A"
                else:
                    qty_str = f"{p.quantity:,.4f}" if p.quantity != int(p.quantity) else f"{int(p.quantity):,}"
                    avg_str = f"{p.avg_cost:,.2f}"
                    curr_str = f"{p.current_price:,.2f}" if p.current_price else "N/A"

                if p.unrealized_pnl is not None and p.unrealized_pnl != 0:
                    emoji = "🟢" if p.unrealized_pnl > 0 else "🔴"
                    pnl_str = f"{emoji} {p.unrealized_pnl:+,.0f}" if market == "IDX" else f"{emoji} {p.unrealized_pnl:+,.2f}"
                else:
                    pnl_str = "—"
                rows.append([p.ticker, qty_str, avg_str, curr_str, pnl_str])

            table = format_pre_table(headers, rows, align_right=[1, 2, 3, 4])
            lines.append(fmt("\n\n", bold(f"📈 {market} MARKET"), "\n", pre(table)))

    keyboard = build_nav_keyboard([
        [("🧠 Analyze", "cmd_analyze"), ("📊 P&L", "cmd_pnl")],
        [("☀️ Briefing", "cmd_briefing")],
    ])
    await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add <market> <ticker> <qty> <price> or /add cash <currency> <amount>."""
    if not _is_authorized(update):
        return
    if not context.args:
        await _reply(update, fmt(
            "⚠️ Usage:\n",
            code("/add <market> <ticker> <qty> <price>"), " — Add position\n",
            code("/add cash <currency> <amount>"), " — Set cash balance"
        ))
        return

    args = context.args

    if args[0].upper() == "CASH":
        if len(args) < 3:
            await _reply(update, fmt("⚠️ Usage: ", code("/add cash IDR 50000000")))
            return
        currency = args[1].upper()
        currency_map = {"US": "USD", "ID": "IDR", "RP": "IDR"}
        currency = currency_map.get(currency, currency)
        amount = parse_decimal(args[2])
        try:
            from datetime import datetime
            from src.models.database import async_session
            from src.models.tables import CashBalance
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            async with async_session() as session:
                stmt = pg_insert(CashBalance).values(currency=currency, balance=amount)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["currency"],
                    set_={"balance": amount, "updated_at": datetime.utcnow()},
                )
                await session.execute(stmt)
                await session.commit()
            await _reply(update, fmt("✅ Cash balance set: ", bold(f"{amount:,.2f} {currency}")))
        except Exception as e:
            logger.error("add_cash_failed", error=str(e))
            await _reply(update, "❌ Operation failed. Check logs.")
        return

    if len(args) < 4:
        await _reply(update, fmt("⚠️ Usage: ", code("/add IDX BBCA 500 8500")))
        return

    market = args[0].upper()
    ticker = args[1].upper()
    if not validate_market(market):
        await _reply(update, "⚠️ Market must be IDX, US, or ETF.")
        return
    if not validate_ticker(ticker):
        await _reply(update, "⚠️ Invalid ticker format.")
        return
    qty = parse_decimal(args[2])
    price = parse_decimal(args[3])

    try:
        from src.models.database import async_session
        from src.models.tables import PortfolioState
        from sqlalchemy import select

        async with async_session() as session:
            result = await session.execute(
                select(PortfolioState).where(PortfolioState.market == market, PortfolioState.ticker == ticker)
            )
            existing = result.scalar_one_or_none()
            if existing:
                await _reply(update, fmt("⚠️ ", bold(ticker), " (", market, ") already exists. Use ", code("/edit"), " to update."))
                return
            session.add(PortfolioState(market=market, ticker=ticker, quantity=qty, avg_cost=price))
            await session.commit()

        pnl_text = ""
        orchestrator = context.bot_data.get("orchestrator")
        if orchestrator:
            try:
                quote = await orchestrator.mcp.get_quote(ticker, market)
                if quote and not quote.get("error") and quote.get("price"):
                    current = quote["price"]
                    pnl = (current - float(price)) * float(qty)
                    pnl_text = f"\n📊 Current: {current:,.2f} | P&L: {pnl:+,.2f}"
                    async with async_session() as session:
                        result = await session.execute(
                            select(PortfolioState).where(PortfolioState.market == market, PortfolioState.ticker == ticker)
                        )
                        pos = result.scalar_one_or_none()
                        if pos:
                            pos.current_price = current
                            pos.unrealized_pnl = pnl
                            await session.commit()
            except Exception:
                pass

        await _reply(update, fmt("✅ Added: ", bold(ticker), " (", market, ") — ", str(qty), " @ ", str(price), pnl_text))
    except Exception as e:
        logger.error("add_position_failed", error=str(e))
        await _reply(update, "❌ Operation failed. Check logs.")


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove <market> <ticker>."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 2:
        await _reply(update, fmt("⚠️ Usage: ", code("/remove <market> <ticker>")))
        return

    market, ticker = context.args[0].upper(), context.args[1].upper()

    try:
        from src.models.database import async_session
        from src.models.tables import PortfolioState
        from sqlalchemy import select

        async with async_session() as session:
            result = await session.execute(
                select(PortfolioState).where(PortfolioState.market == market, PortfolioState.ticker == ticker)
            )
            pos = result.scalar_one_or_none()
            if not pos:
                await _reply(update, fmt("⚠️ ", bold(ticker), " (", market, ") not found in portfolio."))
                return
            await session.delete(pos)
            await session.commit()
        await _reply(update, fmt("✅ Removed: ", bold(ticker), " (", market, ")"))
    except Exception as e:
        logger.error("remove_position_failed", error=str(e))
        await _reply(update, "❌ Operation failed. Check logs.")


async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit <market> <ticker> qty|price <value> or /edit cash <currency> <amount>."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 3:
        await _reply(update, fmt(
            "⚠️ Usage:\n",
            code("/edit <market> <ticker> qty|price <value>"), " — Edit position\n",
            code("/edit cash <currency> <amount>"), " — Edit cash balance"
        ))
        return

    args = context.args

    if args[0].upper() == "CASH":
        if len(args) < 3:
            await _reply(update, fmt("⚠️ Usage: ", code("/edit cash IDR 50000000")))
            return
        currency = args[1].upper()
        currency_map = {"US": "USD", "ID": "IDR", "RP": "IDR"}
        currency = currency_map.get(currency, currency)
        amount = parse_decimal(args[2])
        try:
            from src.models.database import async_session
            from src.models.tables import CashBalance
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(select(CashBalance).where(CashBalance.currency == currency))
                cash = result.scalar_one_or_none()
                if not cash:
                    await _reply(update, fmt("⚠️ No cash balance found for ", currency, ". Use ", code(f"/add cash {currency} <amount>")))
                    return
                cash.balance = amount
                await session.commit()
            await _reply(update, fmt("✅ Cash balance updated: ", bold(f"{amount:,.2f} {currency}")))
        except Exception as e:
            logger.error("edit_cash_failed", error=str(e))
            await _reply(update, "❌ Operation failed. Check logs.")
        return

    if len(args) < 4:
        await _reply(update, fmt("⚠️ Usage: ", code("/edit IDX BBCA qty 600")))
        return

    market, ticker = args[0].upper(), args[1].upper()
    field = args[2].lower()
    value = parse_decimal(args[3])

    if field not in ("qty", "quantity", "price", "avg_cost"):
        await _reply(update, "⚠️ Field must be qty or price.")
        return

    try:
        from src.models.database import async_session
        from src.models.tables import PortfolioState
        from sqlalchemy import select

        async with async_session() as session:
            result = await session.execute(
                select(PortfolioState).where(PortfolioState.market == market, PortfolioState.ticker == ticker)
            )
            pos = result.scalar_one_or_none()
            if not pos:
                await _reply(update, fmt("⚠️ ", bold(ticker), " (", market, ") not found."))
                return
            if field in ("qty", "quantity"):
                pos.quantity = value
            else:
                pos.avg_cost = value
            await session.commit()
        await _reply(update, fmt("✅ Updated: ", bold(ticker), " (", market, ") — ", field, " = ", str(value)))
    except Exception as e:
        logger.error("edit_position_failed", error=str(e))
        await _reply(update, "❌ Operation failed. Check logs.")


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyze or /analyze <ticker> — run portfolio analysis via LLM."""
    if not _is_authorized(update):
        return

    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await _reply(update, "⚠️ System error: Orchestrator not connected.")
        return

    ticker = context.args[0].upper() if context.args else None
    msg = await _reply(update, fmt("🧠 Analyzing ", bold(ticker or "portfolio"), "..."))

    try:
        from src.models.database import async_session
        from src.models.tables import PortfolioState, CashBalance
        from sqlalchemy import select

        async with async_session() as session:
            if ticker:
                port_result = await session.execute(select(PortfolioState).where(PortfolioState.ticker == ticker))
            else:
                port_result = await session.execute(select(PortfolioState).order_by(PortfolioState.market, PortfolioState.ticker))
            positions = port_result.scalars().all()
            cash_result = await session.execute(select(CashBalance))
            cash_balances = cash_result.scalars().all()

        if not positions:
            await msg.edit_text("⚠️ No positions to analyze. Use /add first.")
            return

        portfolio_data = {
            "cash": {c.currency: float(c.balance) for c in cash_balances},
            "holdings": [{"market": p.market, "ticker": p.ticker, "qty": float(p.quantity), "avg_cost": float(p.avg_cost)} for p in positions],
        }

        result = await orchestrator.analyze_portfolio(portfolio_data)

        if result.get("error"):
            await msg.edit_text(f"❌ Analysis failed: {result['error']}")
            return

        from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard
        from collections import defaultdict

        lines = [bold("🧠 PORTFOLIO ANALYSIS")]

        if result.get("portfolio_value"):
            lines.append(fmt(
                "\n💰 ", bold("Value:"), f" {result['portfolio_value']:,.2f} | ",
                bold("P&L:"), f" {result.get('total_unrealized_pnl_pct', 0):+.2f}% | ",
                bold("Cash:"), f" {result.get('cash_pct', 0):.1f}%"
            ))

        holdings = result.get("holdings", [])
        by_market = defaultdict(list)
        for h in holdings:
            by_market[h.get("market", "UNKNOWN")].append(h)

        rec_emoji_map = {"CUT": "🔴", "TRIM": "🟡", "ADD": "🟢", "HOLD": "⚪️"}

        for market in ["IDX", "US", "ETF"]:
            if market not in by_market:
                continue
            headers = ["Action", "Ticker", "P&L", "AI Reasoning"]
            rows = []
            for h in sorted(by_market[market], key=lambda x: {"CUT": 0, "TRIM": 1, "ADD": 2, "HOLD": 3}.get(x.get("recommendation", "HOLD"), 4)):
                rec = h.get("recommendation", "HOLD")
                emoji = rec_emoji_map.get(rec, "⚪️")
                pnl = h.get("unrealized_pnl_pct", 0)
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                reasoning = h.get("reasoning", "")[:80]
                rows.append([f"{emoji} {rec}", h.get("ticker", "?"), f"{pnl_emoji} {pnl:+.1f}%", reasoning])
            table = format_pre_table(headers, rows, align_right=[2])
            lines.append(fmt("\n\n", bold(f"📊 {market} MARKET"), "\n", pre(table)))

        if result.get("top_actions"):
            actions = [f"• {a}" for a in result["top_actions"][:3]]
            lines.append(fmt("\n━━━━━━━━━━━━━━━━\n📌 ", bold("Top Actions:"), "\n", italic(join(actions))))

        if result.get("portfolio_risks"):
            risks = [f"• {r}" for r in result["portfolio_risks"][:3]]
            lines.append(fmt("\n⚠️ ", bold("Portfolio Risks:"), "\n", italic(join(risks))))

        tickers = [h.get("ticker") for h in holdings]
        keyboard = build_nav_keyboard([
            [(f"🔍 {t}", f"audit_{t}") for t in tickers[:3]],
            [("📊 P&L", "cmd_pnl"), ("☀️ Briefing", "cmd_briefing")],
        ])
        await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)

    except Exception as e:
        logger.error("analyze_cmd_failed", error=str(e), exc_info=True)
        await msg.edit_text("❌ Analysis failed. Check logs.")


async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /trades command — show paper trades."""
    if not _is_authorized(update):
        return

    try:
        from src.models.database import async_session
        from src.models.tables import PaperPosition, ClosedPaperTrade
        from sqlalchemy import select, func

        async with async_session() as session:
            open_result = await session.execute(select(PaperPosition).order_by(PaperPosition.entry_date.desc()))
            open_positions = open_result.scalars().all()

            closed_result = await session.execute(select(ClosedPaperTrade).order_by(ClosedPaperTrade.exit_date.desc()).limit(10))
            closed_trades = closed_result.scalars().all()

            pnl_result = await session.execute(select(
                func.sum(ClosedPaperTrade.realized_pnl).label("total_pnl"),
                func.count(ClosedPaperTrade.id).label("total_trades")
            ))
            pnl_summary = pnl_result.one()

        from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard

        lines = [bold("📋 PAPER TRADING HISTORY"), "\n"]

        if open_positions:
            lines.append(bold("🟢 OPEN POSITIONS"))
            headers = ["Ticker", "Dir", "Entry", "Curr", "P&L"]
            rows = []
            for pos in open_positions:
                emoji = "🟢L" if pos.side == "LONG" else "🔴S"
                pnl = pos.unrealized_pnl_pct or 0
                pnl_str = f"🟢 +{pnl:.1f}%" if pnl >= 0 else f"🔴 {pnl:.1f}%"
                rows.append([pos.ticker, emoji, f"{pos.entry_price:.2f}", f"{pos.current_price or 0:.2f}", pnl_str])
            table = format_pre_table(headers, rows, align_right=[2, 3, 4])
            lines.append(pre(table))
        else:
            lines.append(italic("📭 No open positions."))

        if closed_trades:
            lines.extend(["\n", bold("🏁 RECENT CLOSED TRADES")])
            headers = ["Ticker", "Result", "P&L", "Reason"]
            rows = []
            for t in closed_trades[:5]:
                pnl = t.realized_pnl_pct or 0
                res = "🟢 Win" if pnl > 0 else "🔴 Loss"
                pnl_str = f"🟢 +{pnl:.1f}%" if pnl >= 0 else f"🔴 {pnl:.1f}%"
                reason = (t.exit_reason or "N/A")[:10]
                rows.append([t.ticker, res, pnl_str, reason])
            table = format_pre_table(headers, rows, align_right=[2])
            lines.append(pre(table))

        if pnl_summary.total_trades:
            total_pnl = pnl_summary.total_pnl or 0
            emoji = "🟢" if total_pnl >= 0 else "🔴"
            lines.extend([
                "\n━━━━━━━━━━━━━━━━\n",
                fmt(emoji, " ", bold("Total Realized P&L:"), f" {total_pnl:+,.2f}"),
                "\n", bold("Total Trades:"), f" {pnl_summary.total_trades}",
            ])

        keyboard = build_nav_keyboard([
            [("📊 P&L", "cmd_pnl"), ("☀️ Briefing", "cmd_briefing")],
        ])
        await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)

    except Exception as e:
        logger.error("trades_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ Operation failed. Check logs.")


async def briefing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /briefing command — Morning Dashboard."""
    if not _is_authorized(update):
        return

    try:
        from datetime import datetime
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

        orchestrator = context.bot_data.get("orchestrator")
        if not orchestrator:
            await _reply(update, "⚠️ System error: Orchestrator not connected.")
            return

        msg = await _reply(update, bold("📊 Generating Morning Briefing..."))

        from src.advisory.regime import USRegimeFilter, IDXRegimeFilter
        import asyncio

        us_filter = USRegimeFilter(orchestrator.mcp)
        idx_filter = IDXRegimeFilter(orchestrator.mcp)
        us_regime, idx_regime = await asyncio.gather(
            us_filter.get_current_regime(),
            idx_filter.get_current_regime(),
        )

        from src.models.database import async_session
        from src.models.tables import PortfolioState, CashBalance, PaperPosition
        from sqlalchemy import select

        async with async_session() as session:
            port_result = await session.execute(select(PortfolioState))
            positions = port_result.scalars().all()
            cash_result = await session.execute(select(CashBalance))
            cash_balances = cash_result.scalars().all()
            paper_result = await session.execute(select(PaperPosition))
            paper_positions = paper_result.scalars().all()

        portfolio_value = sum(float(c.balance) for c in cash_balances)
        for pos in positions:
            if pos.current_price:
                portfolio_value += float(pos.quantity * pos.current_price)

        total_cash = sum(float(c.balance) for c in cash_balances)
        cash_pct = (total_cash / portfolio_value * 100) if portfolio_value > 0 else 0
        paper_pnl = sum(float(p.unrealized_pnl or 0) for p in paper_positions)

        def _emoji(state: str) -> str:
            return "🟢" if state == "BULL" else "🔴" if state == "BEAR" else "🟡"

        us_e = _emoji(us_regime.get("state", "UNKNOWN"))
        idx_e = _emoji(idx_regime.get("state", "UNKNOWN"))

        regime_block = (
            f"US   : {us_e} {us_regime.get('state', 'UNKNOWN')}\n"
            f"     SPY {us_regime.get('benchmark_price', 'N/A')} | VIX {us_regime.get('vix', 'N/A')}\n"
            f"IDX  : {idx_e} {idx_regime.get('state', 'UNKNOWN')}\n"
            f"     {idx_regime.get('benchmark', 'IHSG')} {idx_regime.get('benchmark_price', 'N/A')}\n"
            f"Rec  : {us_regime.get('recommendation', 'N/A')}"
        )

        port_block = (
            f"Total Value : {portfolio_value:,.2f}\n"
            f"Cash        : {total_cash:,.2f} ({cash_pct:.1f}%)\n"
            f"Positions   : {len(positions)} open"
        )

        paper_block = (
            f"Open Trades : {len(paper_positions)}\n"
            f"Unrealized  : {'🟢' if paper_pnl >= 0 else '🔴'} {paper_pnl:+,.2f}"
        )

        # IDX Intelligence Summary
        idx_intel_block = ""
        try:
            intel = orchestrator.idx_intel
            composite = await intel.get_regime_composite()
            idx_state = composite.get("state", "UNKNOWN")
            idx_score = composite.get("score", 0)
            idx_emoji = {"STRONG_BULL": "🟢🟢", "BULL": "🟢", "NEUTRAL": "🟡", "BEAR": "🔴", "STRONG_BEAR": "🔴🔴"}.get(idx_state, "⚪️")

            # Top sectors
            sectors = composite.get("components", {}).get("sector", {}).get("sectors", [])
            top_sector = sectors[0]["sector"] if sectors else "—"
            top_sector_chg = f"{sectors[0]['avg_change_pct']:+.1f}%" if sectors else ""

            # Earnings watchlist
            blackout = intel.earnings.get_blackout_universe()
            earnings_note = f"⚠️ {', '.join(blackout)}" if blackout else "Clear"

            idx_intel_block = (
                f"Composite : {idx_emoji} {idx_state} ({idx_score:+.0f})\n"
                f"Top Sector: {top_sector} {top_sector_chg}\n"
                f"Earnings  : {earnings_note}"
            )
        except Exception:
            idx_intel_block = "Unavailable"

        text = fmt(
            bold("☀️ MORNING BRIEFING"), "\n",
            italic(datetime.now().strftime('%a, %b %d | %H:%M')), "\n\n",
            bold("🌡️ REGIME & CONTEXT"), "\n", pre(regime_block), "\n\n",
            bold("🇮🇩 IDX INTELLIGENCE"), "\n", pre(idx_intel_block), "\n\n",
            bold("💼 PORTFOLIO STATUS"), "\n", pre(port_block), "\n\n",
            bold("📈 PAPER TRADING"), "\n", pre(paper_block)
        )

        keyboard = build_nav_keyboard([
            [("🇮🇩 IDX Detail", "idx_overview"), ("🌡️ Deep Regime", "cmd_regime")],
            [("📈 View P&L", "cmd_pnl"), ("📋 Open Trades", "cmd_trades")],
            [("💼 Portfolio", "cmd_portfolio")],
        ])
        await send_long_message(update, str(text), reply_markup=keyboard)

    except Exception as e:
        logger.error("briefing_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ Briefing failed. Check logs.")


async def regime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /regime command — Market State (US + IDX)."""
    if not _is_authorized(update):
        return

    try:
        from src.advisory.regime import USRegimeFilter, IDXRegimeFilter

        orchestrator = context.bot_data.get("orchestrator")
        if not orchestrator:
            await _reply(update, "⚠️ System error: Orchestrator not connected.")
            return

        await _reply(update, bold("🌡️ Fetching market regimes..."))

        us_filter = USRegimeFilter(orchestrator.mcp)
        idx_filter = IDXRegimeFilter(orchestrator.mcp)

        import asyncio
        us_regime, idx_regime = await asyncio.gather(
            us_filter.get_current_regime(),
            idx_filter.get_current_regime(),
        )

        def _emoji(state: str) -> str:
            return "🟢" if state == "BULL" else "🔴" if state == "BEAR" else "🟡"

        us_e = _emoji(us_regime.get("state", "UNKNOWN"))
        idx_e = _emoji(idx_regime.get("state", "UNKNOWN"))

        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

        us_block = (
            f"State  : {us_e} {us_regime.get('state', 'UNKNOWN')}\n"
            f"SPY    : {us_regime.get('benchmark_price', 'N/A')} (200 SMA: {us_regime.get('sma200', 'N/A')})\n"
            f"VIX    : {us_regime.get('vix', 'N/A')}\n"
            f"Rec    : {us_regime.get('recommendation', 'N/A')}"
        )

        idx_block = (
            f"State  : {idx_e} {idx_regime.get('state', 'UNKNOWN')}\n"
            f"{idx_regime.get('benchmark', 'IHSG')}  : {idx_regime.get('benchmark_price', 'N/A')} (200 SMA: {idx_regime.get('sma200', 'N/A')})\n"
            f"BBCA   : {idx_regime.get('bbca_price', 'N/A')}\n"
            f"Rec    : {idx_regime.get('recommendation', 'N/A')}"
        )

        text = fmt(
            bold("🌡️ MARKET REGIME"), "\n\n",
            bold("🇺🇸 US MARKET"), "\n", pre(us_block), "\n\n",
            bold("🇮🇩 IDX MARKET"), "\n", pre(idx_block)
        )

        keyboard = build_nav_keyboard([
            [("☀️ Briefing", "cmd_briefing"), ("📊 P&L", "cmd_pnl")],
            [("💼 Portfolio", "cmd_portfolio")],
        ])
        await send_long_message(update, str(text), reply_markup=keyboard)

    except Exception as e:
        logger.error("regime_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ Regime check failed. Check logs.")


async def idx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /idx command — IDX Intelligence Dashboard.

    Panels: overview (default), sector, breadth, flow, earnings.
    Usage: /idx or /idx sector
    """
    if not _is_authorized(update):
        return

    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await _reply(update, "⚠️ System error: Orchestrator not connected.")
        return

    panel = (context.args[0].lower() if context.args else "overview").strip()
    msg = await _reply(update, bold("🇮🇩 Loading IDX Intelligence..."))

    try:
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard, format_pre_table

        intel = orchestrator.idx_intel

        if panel == "sector":
            sectors = await intel.get_sector_performance()
            headers = ["Sector", "Chg%", "A/D", "Flow", "Signal"]
            rows = []
            rotation_emoji = {"LEADING": "🟢", "IMPROVING": "🔵", "NEUTRAL": "⚪️", "WEAKENING": "🟡", "LAGGING": "🔴"}
            for s in sectors:
                remoji = rotation_emoji.get(s["rotation_signal"], "⚪️")
                rows.append([
                    s["sector"],
                    f"{s['avg_change_pct']:+.2f}%",
                    f"{s['advancers']}/{s['decliners']}",
                    f"{s['foreign_flow']:+.1f}",
                    f"{remoji} {s['rotation_signal']}",
                ])
            table = format_pre_table(headers, rows, align_right=[1, 2, 3])
            text = str(fmt(
                bold("📊 IDX SECTOR ROTATION"), "\n",
                pre(table), "\n\n",
                italic("LEADING = strong + buying | LAGGING = weak + selling")
            ))

        elif panel == "breadth":
            breadth = await intel.get_breadth_metrics()
            bar_len = 20
            adv_bar = int(breadth["advancing_pct"] / 100 * bar_len)
            bar = "█" * adv_bar + "░" * (bar_len - adv_bar)
            text = str(fmt(
                bold("📈 IDX MARKET BREADTH"), "\n\n",
                bold("Advancing:"), f" {breadth['advancing']}", "\n",
                bold("Declining:"), f" {breadth['declining']}", "\n",
                bold("Unchanged:"), f" {breadth['unchanged']}", "\n\n",
                bold("Breadth Ratio:"), f" {breadth['breadth_ratio']:.2f}", "\n",
                bold("Advancing %:"), f" {breadth['advancing_pct']:.1f}%", "\n\n",
                pre(f"[{bar}] {breadth['advancing_pct']:.0f}%")
            ))

        elif panel == "flow":
            from src.agents.orchestrator import IDX_UNIVERSE
            flows = []
            for ticker in IDX_UNIVERSE[:10]:
                try:
                    f = await intel.flow_tracker.get_ticker_flow(ticker)
                    flows.append(f)
                except Exception:
                    continue

            headers = ["Ticker", "3d Flow%", "Signal"]
            rows = []
            flow_emoji = {"STRONG_BUY": "🟢🟢", "BUY": "🟢", "NEUTRAL": "⚪️", "SELL": "🔴", "STRONG_SELL": "🔴🔴"}
            for f in sorted(flows, key=lambda x: x.get("net_flow_3d_pct", 0), reverse=True):
                emoji = flow_emoji.get(f["signal"], "⚪️")
                rows.append([f["ticker"], f"{f['net_flow_3d_pct']:+.1f}", f"{emoji} {f['signal']}"])
            table = format_pre_table(headers, rows, align_right=[1])
            text = str(fmt(
                bold("💰 IDX FOREIGN FLOW (3-Day Proxy)"), "\n",
                pre(table), "\n\n",
                italic("Proxy: volume surge + price direction (no broker feed)")
            ))

        elif panel == "earnings":
            upcoming = intel.earnings.get_upcoming(days=30)
            blackout = intel.earnings.get_blackout_universe()
            lines = [bold("📅 IDX EARNINGS CALENDAR"), "\n"]
            if blackout:
                lines.extend([bold("⚠️ BLACKOUT:"), f" {', '.join(blackout)}", "\n\n"])
            if upcoming:
                headers = ["Ticker", "Date", "FQ", "Days"]
                rows = [[e["ticker"], e["report_date"], e["fiscal_quarter"] or "—", str(e["days_until"])] for e in upcoming]
                table = format_pre_table(headers, rows, align_right=[3])
                lines.extend([pre(table)])
            else:
                lines.append(italic("No upcoming earnings in next 30 days."))
            text = str(fmt(*lines))

        else:  # overview (default)
            composite = await intel.get_regime_composite()
            state_emoji = {
                "STRONG_BULL": "🟢🟢", "BULL": "🟢", "NEUTRAL": "🟡",
                "BEAR": "🔴", "STRONG_BEAR": "🔴🔴",
            }
            emoji = state_emoji.get(composite["state"], "⚪️")
            score_bar_len = 20
            score_pos = int((composite["score"] + 100) / 200 * score_bar_len)
            score_bar = "░" * score_pos + "█" + "░" * (score_bar_len - score_pos - 1)

            components = composite.get("components", {})
            breadth_c = components.get("breadth", {})
            sector_c = components.get("sector", {})
            flow_c = components.get("flow", {})
            price_c = components.get("price", {})

            detail_block = (
                f"Breadth : {breadth_c.get('score', 0):+.0f} (wt {breadth_c.get('weight', 0)*100:.0f}%)\n"
                f"Sector  : {sector_c.get('score', 0):+.0f} (wt {sector_c.get('weight', 0)*100:.0f}%)\n"
                f"Flow    : {flow_c.get('score', 0):+.0f} (wt {flow_c.get('weight', 0)*100:.0f}%)\n"
                f"Price   : {price_c.get('score', 0):+.0f} (wt {price_c.get('weight', 0)*100:.0f}%)"
            )

            triggers = composite.get("triggers", [])
            trigger_lines = "\n".join(f"  • {t}" for t in triggers[:5]) if triggers else "  None"

            text = str(fmt(
                bold("🇮🇩 IDX INTELLIGENCE DASHBOARD"), "\n\n",
                bold("Composite Score:"), f" {composite['score']:+.0f}/100  ", emoji, f" {composite['state']}", "\n",
                pre(f"[{score_bar}] {composite['score']:+.0f}"), "\n\n",
                bold("Components:"), "\n", pre(detail_block), "\n\n",
                bold("Active Triggers:"), "\n", trigger_lines,
            ))

        keyboard = build_nav_keyboard([
            [("📊 Sector", "idx_sector"), ("📈 Breadth", "idx_breadth")],
            [("💰 Flow", "idx_flow"), ("📅 Earnings", "idx_earnings")],
            [("🔙 Overview", "idx_overview"), ("💼 Portfolio", "cmd_portfolio")],
        ])
        await send_long_message(update, text, reply_markup=keyboard)

    except Exception as e:
        logger.error("idx_cmd_failed", error=str(e), exc_info=True)
        await msg.edit_text("❌ IDX Intelligence failed. Check logs.")


async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pnl command — Shadow Portfolio Performance."""
    if not _is_authorized(update):
        return

    try:
        from src.models.database import async_session
        from src.models.tables import PaperPosition, ClosedPaperTrade
        from sqlalchemy import select, func

        async with async_session() as session:
            open_result = await session.execute(select(PaperPosition))
            open_positions = open_result.scalars().all()

            closed_result = await session.execute(select(
                func.sum(ClosedPaperTrade.realized_pnl).label("total_pnl"),
                func.count(ClosedPaperTrade.id).label("total_trades"),
                func.avg(ClosedPaperTrade.realized_pnl_pct).label("avg_pnl_pct")
            ))
            stats = closed_result.one()

            win_result = await session.execute(select(func.count(ClosedPaperTrade.id)).where(ClosedPaperTrade.realized_pnl > 0))
            wins = win_result.scalar() or 0

            loss_result = await session.execute(select(func.count(ClosedPaperTrade.id)).where(ClosedPaperTrade.realized_pnl <= 0))
            losses = loss_result.scalar() or 0

        total_pnl = stats.total_pnl or 0
        total_trades = stats.total_trades or 0
        avg_pnl_pct = stats.avg_pnl_pct or 0
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        open_pnl = sum(float(p.unrealized_pnl or 0) for p in open_positions)
        open_pnl_pct = sum(float(p.unrealized_pnl_pct or 0) for p in open_positions) / len(open_positions) if open_positions else 0

        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

        open_block = (
            f"Count       : {len(open_positions)}\n"
            f"Unrealized  : {'🟢' if open_pnl >= 0 else '🔴'} {open_pnl:+,.2f} ({open_pnl_pct:+.1f}%)"
        )

        closed_block = (
            f"Total       : {total_trades}\n"
            f"Wins/Losses : {wins}W / {losses}L\n"
            f"Win Rate    : {win_rate:.1f}%\n"
            f"Realized    : {'🟢' if total_pnl >= 0 else '🔴'} {total_pnl:+,.2f}\n"
            f"Avg P&L     : {avg_pnl_pct:+.1f}%"
        )

        text = fmt(
            bold("📊 SHADOW PORTFOLIO P&L"), "\n\n",
            bold("🟢 OPEN POSITIONS"), "\n", pre(open_block), "\n\n",
            bold("🏁 CLOSED TRADES"), "\n", pre(closed_block)
        )

        keyboard = build_nav_keyboard([
            [("📋 Trades", "cmd_trades"), ("☀️ Briefing", "cmd_briefing")],
            [("📈 Regime", "cmd_regime")],
        ])
        await send_long_message(update, str(text), reply_markup=keyboard)

    except Exception as e:
        logger.error("pnl_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ P&L check failed. Check logs.")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /audit <ticker> or /audit portfolio."""
    if not _is_authorized(update):
        return

    if not context.args:
        await _reply(update, fmt(
            "⚠️ Usage:\n",
            code("/audit <TICKER>"), " — audit one signal\n",
            code("/audit portfolio"), " — audit all holdings"
        ))
        return

    arg = context.args[0].upper()

    try:
        from src.models.database import async_session
        from src.models.tables import Signal, PortfolioState
        from sqlalchemy import select, desc
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard, format_pre_table
        from datetime import datetime

        if arg == "PORTFOLIO":
            msg = await _reply(update, bold("🔍 Auditing portfolio signals..."))

            async with async_session() as session:
                port_result = await session.execute(select(PortfolioState).order_by(PortfolioState.market, PortfolioState.ticker))
                positions = port_result.scalars().all()

            if not positions:
                await msg.edit_text("📭 No positions to audit. Use /add first.")
                return

            tickers_by_market = {}
            for p in positions:
                tickers_by_market.setdefault(p.market, []).append(p.ticker)

            async with async_session() as session:
                signals = {}
                for p in positions:
                    result = await session.execute(
                        select(Signal).where(Signal.ticker == p.ticker, Signal.market == p.market)
                        .order_by(desc(Signal.created_at)).limit(1)
                    )
                    sig = result.scalar_one_or_none()
                    if sig:
                        signals[p.ticker] = sig

            lines = [bold(f"🔍 PORTFOLIO AUDIT — {len(signals)}/{len(positions)} with signals"), "\n"]

            for market in ["IDX", "US", "ETF"]:
                market_tickers = tickers_by_market.get(market, [])
                if not market_tickers:
                    continue
                headers = ["Ticker", "Dir", "Strat", "Conf", "R:R", "Generated"]
                rows = []
                for t in market_tickers:
                    sig = signals.get(t)
                    if not sig:
                        rows.append([t, "—", "—", "—", "—", "No signal"])
                        continue
                    emoji = "🟢" if sig.direction == "LONG" else "🔴" if sig.direction == "SHORT" else "⚪️"
                    rr = f"{sig.risk_reward_ratio:.1f}" if sig.risk_reward_ratio else "—"
                    conf = f"{sig.confidence_score}/100" if sig.confidence_score else "—"
                    ts = sig.created_at.strftime("%m-%d %H:%M") if sig.created_at else "—"
                    rows.append([t, f"{emoji} {sig.direction}", (sig.strategy or "?")[:10], conf, rr, ts])
                table = format_pre_table(headers, rows, align_right=[3, 4])
                lines.append(fmt("\n", bold(f"📈 {market}"), "\n", pre(table)))

            if signals:
                latest = max(signals.values(), key=lambda s: s.created_at or datetime.min)
                if latest.reasoning:
                    lines.append(fmt("\n🧠 ", bold(f"Latest signal ({latest.ticker}):"), "\n", italic(latest.reasoning[:300])))

            keyboard = build_nav_keyboard([
                [("🧠 Analyze", "cmd_analyze"), ("🔍 Scan", "cmd_portfolio")],
                [("📊 P&L", "cmd_pnl"), ("☀️ Briefing", "cmd_briefing")],
            ])
            try:
                await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)
            except Exception as send_err:
                logger.error("audit_send_failed", error=str(send_err))
                try:
                    await _reply(update, fmt("📊 Audit: ", bold(str(len(signals))), " signals found. Check /scan for details."))
                except Exception:
                    pass
            return

        # /audit <ticker>
        ticker = arg
        async with async_session() as session:
            result = await session.execute(
                select(Signal).where(Signal.ticker == ticker).order_by(desc(Signal.created_at)).limit(1)
            )
            signal = result.scalar_one_or_none()

        if not signal:
            await _reply(update, fmt("📭 No signal found for ", bold(ticker), ". Run ", code("/scan"), " to generate one."))
            return

        d_emoji = "🟢" if signal.direction == "LONG" else "🔴" if signal.direction == "SHORT" else "⚪️"

        metrics_block = (
            f"Decision    : {d_emoji} {signal.direction}\n"
            f"Strategy    : {signal.strategy}\n"
            f"Confidence  : {signal.confidence_score}/100\n"
            f"Market      : {signal.market}"
        )

        lines = [
            bold(f"🔍 AUDIT LOG: {ticker}"), "\n",
            italic(f"Generated: {signal.created_at.strftime('%Y-%m-%d %H:%M') if signal.created_at else 'N/A'}"), "\n\n",
            bold("📊 SIGNAL METRICS"), "\n", pre(metrics_block),
        ]

        if signal.entry_price:
            pricing_block = (
                f"Entry       : {signal.entry_price:,.2f}\n"
                f"Target      : {signal.target_price:,.2f}\n"
                f"Stop Loss   : {signal.stop_loss_price:,.2f}\n"
                f"Risk/Reward : {signal.risk_reward_ratio:.2f}"
            )
            lines.extend(["\n\n", bold("💰 PRICING"), "\n", pre(pricing_block)])

        if signal.reasoning:
            lines.extend(["\n\n", bold("🧠 AI REASONING"), "\n", italic(signal.reasoning[:500])])

        keyboard = build_nav_keyboard([
            [("💼 Portfolio", "cmd_portfolio"), ("☀️ Briefing", "cmd_briefing")],
        ])
        try:
            await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)
        except Exception as send_err:
            logger.error("audit_send_failed", error=str(send_err))
            try:
                summary = fmt(
                    bold(f"📊 Audit: {ticker}"), "\n",
                    "Direction: ", signal.direction, "\n",
                    "Confidence: ", str(signal.confidence_score), "/100"
                )
                await _reply(update, summary)
            except Exception:
                pass

    except Exception as e:
        logger.error("audit_cmd_failed", error=str(e), exc_info=True)
        try:
            await _reply(update, "❌ Audit failed. Check logs.")
        except Exception:
            pass


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency kill switch — halts all new trading decisions immediately."""
    if not _is_authorized(update):
        return

    operator = update.effective_user.username or str(update.effective_user.id)
    already_active = await emergency.is_active()

    if already_active:
        await _reply(update, "⚠️ Emergency stop is already active.")
        return

    await emergency.activate(reason="Manual operator halt via Telegram", operator=operator)
    logger.warning("emergency_stop_activated", operator=operator)
    await _reply(update, fmt(
        "🚨 ", bold("EMERGENCY STOP ACTIVATED"), "\n",
        "All new trading decisions are halted.\n",
        "Use ", code("/resume"), " to reactivate."
    ))


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume trading after emergency stop."""
    if not _is_authorized(update):
        return

    operator = update.effective_user.username or str(update.effective_user.id)
    is_active = await emergency.is_active()

    if not is_active:
        await _reply(update, "✅ Emergency stop is not active. Trading is normal.")
        return

    await emergency.deactivate(operator=operator)
    logger.warning("emergency_stop_deactivated", operator=operator)
    await _reply(update, fmt(
        "✅ ", bold("Emergency stop deactivated."), "\n",
        "Trading decisions can resume."
    ))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks from inline keyboards."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("approve_"):
        await handle_approval(update, data[8:], "approve", _is_authorized)
        return
    if data.startswith("reject_"):
        await handle_approval(update, data[7:], "reject", _is_authorized)
        return

    if data.startswith("audit_"):
        ticker = data[6:]
        context.args = [ticker]
        await audit_cmd(update, context)
        return

    if data.startswith("idx_"):
        panel = data[4:]
        context.args = [panel]
        await idx_cmd(update, context)
        return

    if not data.startswith("cmd_"):
        return

    cmd = data[4:]

    if cmd == "pnl":
        await pnl_cmd(update, context)
    elif cmd == "regime":
        await regime_cmd(update, context)
    elif cmd == "trades":
        await trades_cmd(update, context)
    elif cmd == "portfolio":
        await portfolio_cmd(update, context)
    elif cmd == "briefing":
        await briefing_cmd(update, context)
    elif cmd == "audit":
        await audit_cmd(update, context)
    elif cmd == "analyze":
        await analyze_cmd(update, context)
    elif cmd == "guide":
        await guide_cmd(update, context)
