"""Karsa Trading System - Telegram Bot Command Handlers"""

from decimal import Decimal, InvalidOperation
from src.utils.validation import validate_ticker, validate_market, sanitize_for_prompt
from src.utils.telegram_helpers import escape_html
from telegram import Update
from telegram.ext import ContextTypes
import httpx

from src.config import settings, LLM_BASE_URL
from src.utils.logging import get_logger
from src.risk import emergency

logger = get_logger("telegram_handlers")


def parse_decimal(raw: str) -> Decimal:
    """Parse number string handling both comma and dot as decimal separator.

    Rules:
    - Contains both . and , → last one is decimal separator (1,234.56 or 1.234,56)
    - Contains only , → decimal separator (0,006421695)
    - Contains only . → decimal separator (0.006421695)
    - Neither → integer

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


async def _reply(update: Update, text: str, add_timestamp: bool = True, **kwargs):
    """Reply to message or edit callback query message."""
    if add_timestamp:
        from datetime import datetime, timezone
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        text = f"<i>{ts}</i>\n{text}"

    if update.callback_query:
        return await update.callback_query.message.edit_text(text, **kwargs)
    elif update.message:
        return await update.message.reply_text(text, **kwargs)
    return None


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await _reply(update,
        "<b>🤖 Karsa Advisory Desk</b>\n\n"
        "AI-driven trading desk for IDX, US, and ETF markets.\n"
        "Use /guide for full walkthrough.\n\n"
        "<b>Quick Commands:</b>\n"
        "/portfolio - View holdings &amp; cash\n"
        "/briefing - Morning dashboard\n"
        "/scan &lt;market&gt; &lt;ticker&gt; - Scan a stock\n"
        "/analyze - AI portfolio review\n"
        "/regime - Market state\n"
        "/guide - Full 101 walkthrough\n",
        parse_mode="HTML",
    )


async def guide_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /guide — full walkthrough of Karsa."""
    if not _is_authorized(update):
        return

    from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

    text = (
        "<b>📖 KARSA 101 — Your AI Trading Desk</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🤖 What is Karsa?</b>\n"
        "Karsa is an AI-powered advisory desk that scans markets, generates signals, "
        "and tracks a shadow paper portfolio — all through Telegram. "
        "It covers IDX (Indonesia), US Equities, and Global ETFs.\n\n"
        "Karsa does NOT trade your real money. It provides analysis and paper-trades "
        "to help you make informed decisions. You approve or reject every signal.\n\n"

        "<b>📋 SUPPORTED MARKETS</b>\n"
        "• <b>IDX</b> — Indonesian stocks (BBCA, BBRI, BMRI, TLKM ...)\n"
        "• <b>US</b> — US equities (NVDA, AAPL, MSFT, GOOGL ...)\n"
        "• <b>ETF</b> — Global ETFs (SPY, QQQ, GLD, TLT ...)\n\n"

        "<b>🔄 HOW IT WORKS — Step by Step</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>Step 1: Set Up Your Portfolio</b>\n"
        "Tell Karsa what you hold so it can track and analyze.\n"
        "  <code>/add IDX BBCA 500 8500</code> — add 500 BBCA @ 8,500\n"
        "  <code>/add US NVDA 10 120.50</code> — add 10 NVDA @ $120.50\n"
        "  <code>/add cash IDR 50000000</code> — set IDR cash\n"
        "  <code>/add cash USD 10000</code> — set USD cash\n\n"

        "<b>Step 2: Start Your Day with /briefing</b>\n"
        "Morning dashboard shows:\n"
        "  • Market regime (BULL / NEUTRAL / BEAR)\n"
        "  • Portfolio value &amp; cash ratio\n"
        "  • Paper trading open positions\n\n"

        "<b>Step 3: Scan Markets</b>\n"
        "  <code>/scan IDX BBCA</code> — quick AI readout on one ticker\n"
        "  <code>/scan portfolio</code> — scan ALL your holdings at once\n"
        "  <code>/analyze</code> — full portfolio analysis with AI recommendations\n"
        "  <code>/analyze NVDA</code> — deep dive on a single holding\n\n"

        "<b>Step 4: Review Signals</b>\n"
        "When Karsa finds an opportunity (confidence ≥ 60/100), it:\n"
        "  1. Sends you a Telegram alert with APPROVE / REJECT buttons\n"
        "  2. If approved → paper trade executed automatically\n"
        "  3. If rejected → signal discarded\n\n"

        "<b>Step 5: Track Performance</b>\n"
        "  <code>/portfolio</code> — view all holdings, avg cost, unrealized P&amp;L\n"
        "  <code>/pnl</code> — shadow portfolio: open P&amp;L, win rate, realized P&amp;L\n"
        "  <code>/trades</code> — paper trading history (open + closed)\n"
        "  <code>/audit NVDA</code> — see AI reasoning behind a signal\n"
        "  <code>/audit portfolio</code> — audit signals for all holdings\n\n"

        "<b>Step 6: Manage Positions</b>\n"
        "  <code>/edit IDX BBCA qty 600</code> — update quantity\n"
        "  <code>/edit IDX BBCA price 9000</code> — update avg cost\n"
        "  <code>/remove IDX BBCA</code> — remove position\n"
        "  <code>/edit cash IDR 60000000</code> — update cash balance\n\n"

        "<b>📊 COMMAND REFERENCE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>Portfolio:</b>\n"
        "  /portfolio — view all holdings &amp; cash\n"
        "  /add — add position or cash\n"
        "  /remove — remove position\n"
        "  /edit — edit position or cash\n\n"

        "<b>Analysis:</b>\n"
        "  /scan &lt;market&gt; &lt;ticker&gt; — quick scan\n"
        "  /scan portfolio — scan all holdings\n"
        "  /analyze — full portfolio AI review\n"
        "  /analyze &lt;ticker&gt; — single holding deep dive\n"
        "  /audit &lt;ticker&gt; — signal audit trail\n"
        "  /audit portfolio — audit all holdings\n\n"

        "<b>CIO Dashboard:</b>\n"
        "  /briefing — morning dashboard (US + IDX regime)\n"
        "  /regime — market regime (US + IDX)\n"
        "  /pnl — shadow portfolio P&amp;L\n"
        "  /trades — paper trade history\n\n"

        "<b>System:</b>\n"
        "  /status — health check\n"
        "  /guide — this guide\n\n"

        "<b>🧠 THE AI AGENTS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Karsa runs specialized AI agents:\n"
        "• <b>IDX Analyst</b> — scans Indonesian stocks\n"
        "• <b>US Analyst</b> — scans US equities\n"
        "• <b>ETF Analyst</b> — scans global ETFs\n"
        "• <b>Portfolio Analyst</b> — analyzes your holdings\n"
        "• <b>Regime Filter</b> — macro context (VIX, SPY, IHSG)\n\n"
        "Each agent uses live market data (TradingView), "
        "technical indicators (RSI, Bollinger, EMA), "
        "and AI reasoning to generate signals.\n\n"

        "<b>⚠️ IMPORTANT NOTES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Karsa is an <b>advisory system</b>, not a broker\n"
        "• All trades are <b>paper trades</b> (shadow portfolio)\n"
        "• You decide what to execute in your real account\n"
        "• Kill switch triggers at -1.5% daily P&amp;L\n"
        "• Signals expire after 24 hours if not acted on\n"
    )

    keyboard = build_nav_keyboard([
        [("💼 Portfolio", "cmd_portfolio"), ("☀️ Briefing", "cmd_briefing")],
        [("🌡️ Regime", "cmd_regime"), ("📊 P&L", "cmd_pnl")],
    ])

    await send_long_message(update, text, reply_markup=keyboard)


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan <market> <ticker> or /scan portfolio."""
    if not _is_authorized(update):
        return
    if not context.args:
        await _reply(update, "⚠️ Usage:\n`/scan <market> <ticker>` — scan one ticker\n`/scan portfolio` — scan all holdings", parse_mode="Markdown")
        return

    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await _reply(update, "⚠️ System error: Orchestrator not connected.")
        return

    # /scan portfolio
    if context.args[0].upper() == "PORTFOLIO":
        msg = await _reply(update, "🔍 <b>Scanning entire portfolio...</b>", parse_mode="HTML")

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
                await msg.edit_text("📭 No positions to scan. Use `/add` first.", parse_mode="Markdown")
                return

            port_list = [{"market": p.market, "ticker": p.ticker} for p in positions]
            scan_result = await orchestrator.scan_portfolio(port_list)

            lines = [f"<b>🔍 PORTFOLIO SCAN</b> — {len(port_list)} tickers\n"]

            # Group by market
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
                    reasoning = escape_html((r.get("reasoning", "") or "")[:60])
                    rows.append([
                        r.get("ticker", "?"),
                        r.get("strategy", "?")[:12],
                        f"{conf}/100",
                        f"{d_emoji} {direction}",
                        reasoning,
                    ])
                table = format_pre_table(headers, rows, align_right=[2])
                lines.append(f"\n📈 <b>{market}</b>")
                lines.append(f"<pre>{table}</pre>")

            # Errors
            errors = scan_result.get("errors", [])
            if errors:
                lines.append(f"\n⚠️ <b>{len(errors)} failed:</b>")
                for e in errors[:5]:
                    lines.append(f"  • {e['ticker']}: {escape_html(e['error'][:40])}")

            keyboard = build_nav_keyboard([
                [("🧠 Analyze", "cmd_analyze"), ("📊 P&L", "cmd_pnl")],
                [("☀️ Briefing", "cmd_briefing"), ("💼 Portfolio", "cmd_portfolio")],
            ])

            await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

        except Exception as e:
            logger.error("scan_portfolio_failed", error=str(e), exc_info=True)
            await msg.edit_text("❌ Scan failed. Check logs for details.")
        return

    # /scan <market> <ticker>
    if len(context.args) < 2:
        await _reply(update, "⚠️ Usage: `/scan <market> <ticker>`\nExample: `/scan IDX BBCA`", parse_mode="Markdown")
        return

    market, ticker = context.args[0].upper(), context.args[1].upper()
    if not validate_market(market):
        await _reply(update, "⚠️ Market must be IDX, US, or ETF.", parse_mode="Markdown")
        return
    if not validate_ticker(ticker):
        await _reply(update, "⚠️ Invalid ticker format. Use alphanumeric, max 20 chars.", parse_mode="Markdown")
        return
    msg = await _reply(update, f"🔍 Scanning {sanitize_for_prompt(ticker)} ({escape_html(market)})...")

    try:
        result = await orchestrator.scan_single(market, ticker)

        if result.get("error"):
            await msg.edit_text(f"❌ Scan failed: {result['error']}\n\nDetail: {result.get('detail', '')}")
            return

        text = (
            f"<b>ℹ️ Scan: {ticker} ({market})</b>\n"
            f"Strategy: {result.get('strategy', 'Unknown')}\n"
            f"Confidence: {result.get('confidence_score', 0)}/100\n"
            f"Direction: {result.get('direction', 'N/A')}\n\n"
            f"<b>📝 Reasoning:</b>\n{result.get('reasoning', 'No reasoning provided.')}"
        )
        await msg.edit_text(text, parse_mode="HTML")
    except Exception as e:
        logger.error("scan_cmd_failed", error=str(e), exc_info=True)
        await msg.edit_text("❌ Scan failed. Check logs for details.")


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

    # Check Redis (reuse orchestrator's connection)
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
                # Try /v1/models (standard OpenAI endpoint)
                # We don't care about 401/404, just if we can reach it.
                await client.get(f"{router_url}/v1/models")
                router_ok = True
        except Exception:
            pass
        router_status = f"{'🟢' if router_ok else '🔴'} 9Router (`{router_url}`)"

    # Check Scheduler & Jobs (from orchestrator container via HTTP)
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
                            next_run = next_run.split("T")[1][:5]  # Extract HH:MM
                        jobs_info.append(f"  • {job['name']}: next at {next_run}")
            else:
                scheduler_status = "🔴 Scheduler (Unreachable)"
                scheduler_error = f"HTTP {resp.status_code}"
    except Exception as e:
        scheduler_error = str(e)[:100]

    # Check Kill Switch (emergency stop)
    kill_switch_status = "🟢 Kill Switch (Inactive)"
    try:
        stop_active = await emergency.is_active()
        if stop_active:
            stop_info = await emergency.get_status()
            reason = escape_html(stop_info.get("reason", "Unknown")) if stop_info else "Unknown"
            kill_switch_status = f"🔴 Kill Switch (ACTIVE)\n<i>Reason: {reason}</i>"
    except Exception:
        pass

    lines = [
        "<b>📊 System Status</b>\n━━━━━━━━━━━━━━━━",
        f"{'🟢' if db_ok else '🔴'} PostgreSQL",
        f"{'🟢' if redis_ok else '🔴'} Redis",
        router_status,
        "🟢 Orchestrator",
        "",
        "<b>Scheduler &amp; Automation:</b>",
        scheduler_status,
        kill_switch_status,
    ]

    if scheduler_error:
        lines.append(f"<i>⚠️ {scheduler_error}</i>")

    if jobs_info:
        lines.append("\n<b>Scheduled Jobs:</b>")
        lines.extend(jobs_info[:8])  # Show max 8 jobs

    lines.append("━━━━━━━━━━━━━━━━")

    await _reply(update,"\n".join(lines), parse_mode="HTML")


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

        # Fetch live prices for positions missing current_price
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
        await _reply(update, f"❌ Database error: {str(e)}", parse_mode="HTML")
        return

    # Build cash line
    cash_parts = []
    for cash in cash_balances:
        cash_parts.append(f"{cash.balance:,.2f} {cash.currency}")
    cash_str = " | ".join(cash_parts) if cash_parts else "$0.00"

    lines = [
        "<b>💼 PORTFOLIO OVERVIEW</b>",
        f"💵 <b>Cash:</b> {cash_str}",
    ]

    # Group positions by market
    if not positions:
        lines.append("")
        lines.append("<i>📭 No positions open.</i>")
        lines.append("<i>💡 Use /add &lt;market&gt; &lt;ticker&gt; &lt;qty&gt; &lt;price&gt; to add.</i>")
    else:
        for market, market_positions in groupby(positions, key=lambda p: p.market):
            headers = ["Ticker", "Qty", "Avg Cost", "Curr Price", "Unrealized P&L"]
            rows = []
            for p in market_positions:
                # Format qty based on market (IDX uses lots of 100, US supports fractional)
                if market == "IDX":
                    qty_str = f"{int(p.quantity):,}"
                else:
                    qty_str = f"{p.quantity:,.4f}" if p.quantity != int(p.quantity) else f"{int(p.quantity):,}"

                # Format prices based on currency
                if market == "IDX":
                    avg_str = f"{p.avg_cost:,.0f}"
                    curr_str = f"{p.current_price:,.0f}" if p.current_price else "N/A"
                else:
                    avg_str = f"{p.avg_cost:,.2f}"
                    curr_str = f"{p.current_price:,.2f}" if p.current_price else "N/A"

                # Format P&L
                if p.unrealized_pnl is not None and p.unrealized_pnl != 0:
                    emoji = "🟢" if p.unrealized_pnl > 0 else "🔴"
                    if market == "IDX":
                        pnl_str = f"{emoji} {p.unrealized_pnl:+,.0f}"
                    else:
                        pnl_str = f"{emoji} {p.unrealized_pnl:+,.2f}"
                else:
                    pnl_str = "—"
                rows.append([p.ticker, qty_str, avg_str, curr_str, pnl_str])

            table = format_pre_table(headers, rows, align_right=[1, 2, 3, 4])
            lines.append(f"\n📈 <b>{market} MARKET</b>")
            lines.append(f"<pre>{table}</pre>")

    # Inline keyboard
    keyboard = build_nav_keyboard([
        [("🧠 Analyze", "cmd_analyze"), ("📊 P&L", "cmd_pnl")],
        [("☀️ Briefing", "cmd_briefing")],
    ])

    await send_long_message(update, "\n".join(lines), reply_markup=keyboard)


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add <market> <ticker> <qty> <price> or /add cash <currency> <amount>."""
    if not _is_authorized(update):
        return
    if not context.args:
        await _reply(update,
            "⚠️ Usage:\n"
            "`/add <market> <ticker> <qty> <price>` - Add position\n"
            "`/add cash <currency> <amount>` - Set cash balance",
            parse_mode="Markdown"
        )
        return

    args = context.args

    if args[0].upper() == "CASH":
        # /add cash IDR 50000000
        if len(args) < 3:
            await _reply(update,"⚠️ Usage: `/add cash IDR 50000000`", parse_mode="Markdown")
            return
        currency = args[1].upper()
        # Normalize common currency abbreviations
        currency_map = {"US": "USD", "ID": "IDR", "RP": "IDR"}
        currency = currency_map.get(currency, currency)
        amount = parse_decimal(args[2])
        try:
            from src.models.database import async_session
            from src.models.tables import CashBalance
            from sqlalchemy import select

            from datetime import datetime
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            async with async_session() as session:
                stmt = pg_insert(CashBalance).values(currency=currency, balance=amount)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["currency"],
                    set_={"balance": amount, "updated_at": datetime.utcnow()},
                )
                await session.execute(stmt)
                await session.commit()
            await _reply(update,f"✅ Cash balance set: {amount:,.2f} {currency}", parse_mode="Markdown")
        except Exception as e:
            logger.error("add_cash_failed", error=str(e))
            await _reply(update, "❌ Operation failed. Check logs for details.")
        return

    # /add IDX BBCA 500 8500
    if len(args) < 4:
        await _reply(update,"⚠️ Usage: `/add IDX BBCA 500 8500`", parse_mode="Markdown")
        return

    market = args[0].upper()
    ticker = args[1].upper()
    if not validate_market(market):
        await _reply(update, "⚠️ Market must be IDX, US, or ETF.", parse_mode="Markdown")
        return
    if not validate_ticker(ticker):
        await _reply(update, "⚠️ Invalid ticker format.", parse_mode="Markdown")
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
                await _reply(update,
                    f"⚠️ {ticker} ({market}) already exists. Use `/edit` to update.", parse_mode="Markdown"
                )
                return
            session.add(PortfolioState(market=market, ticker=ticker, quantity=qty, avg_cost=price))
            await session.commit()

        # Fetch current price immediately so /portfolio shows data
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
                pass  # Price fetch is best-effort

        await _reply(update,
            f"✅ Added: *{ticker}* ({market}) — {qty} @ {price}{pnl_text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("add_position_failed", error=str(e))
        await _reply(update, "❌ Operation failed. Check logs for details.")


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove <market> <ticker>."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 2:
        await _reply(update,"⚠️ Usage: `/remove <market> <ticker>`\nExample: `/remove IDX BBCA`", parse_mode="Markdown")
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
                await _reply(update,f"⚠️ {ticker} ({market}) not found in portfolio.", parse_mode="Markdown")
                return
            await session.delete(pos)
            await session.commit()
        await _reply(update,f"✅ Removed: *{ticker}* ({market})", parse_mode="Markdown")
    except Exception as e:
        logger.error("remove_position_failed", error=str(e))
        await _reply(update, "❌ Operation failed. Check logs for details.")


async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit <market> <ticker> qty|price <value> or /edit cash <currency> <amount>."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 3:
        await _reply(update,
            "⚠️ Usage:\n"
            "`/edit <market> <ticker> qty|price <value>` - Edit position\n"
            "`/edit cash <currency> <amount>` - Edit cash balance",
            parse_mode="Markdown"
        )
        return

    args = context.args

    # /edit cash IDR 50000000
    if args[0].upper() == "CASH":
        if len(args) < 3:
            await _reply(update,"⚠️ Usage: `/edit cash IDR 50000000`", parse_mode="Markdown")
            return
        currency = args[1].upper()
        # Normalize common currency abbreviations
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
                    await _reply(update,f"⚠️ No cash balance found for {currency}. Use `/add cash {currency} <amount>`.", parse_mode="Markdown")
                    return
                cash.balance = amount
                await session.commit()
            await _reply(update,f"✅ Cash balance updated: {amount:,.2f} {currency}", parse_mode="Markdown")
        except Exception as e:
            logger.error("edit_cash_failed", error=str(e))
            await _reply(update, "❌ Operation failed. Check logs for details.")
        return

    # /edit IDX BBCA qty 600
    if len(args) < 4:
        await _reply(update,
            "⚠️ Usage: `/edit <market> <ticker> qty|price <value>`\nExample: `/edit IDX BBCA qty 600`",
            parse_mode="Markdown"
        )
        return

    market, ticker = args[0].upper(), args[1].upper()
    field = args[2].lower()
    value = parse_decimal(args[3])

    if field not in ("qty", "quantity", "price", "avg_cost"):
        await _reply(update,"⚠️ Field must be `qty` or `price`.", parse_mode="Markdown")
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
                await _reply(update,f"⚠️ {ticker} ({market}) not found.", parse_mode="Markdown")
                return
            if field in ("qty", "quantity"):
                pos.quantity = value
            else:
                pos.avg_cost = value
            await session.commit()
        await _reply(update,
            f"✅ Updated: *{ticker}* ({market}) — {field} = {value}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("edit_position_failed", error=str(e))
        await _reply(update, "❌ Operation failed. Check logs for details.")


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyze or /analyze <ticker> — run portfolio analysis via LLM."""
    if not _is_authorized(update):
        return

    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await _reply(update,"⚠️ System error: Orchestrator not connected.")
        return

    ticker = context.args[0].upper() if context.args else None
    msg_text = f"🧠 Analyzing {'*' + ticker + '*' if ticker else 'portfolio'}..."
    msg = await _reply(update,msg_text, parse_mode="Markdown")

    try:
        from src.models.database import async_session
        from src.models.tables import PortfolioState, CashBalance
        from sqlalchemy import select

        async with async_session() as session:
            if ticker:
                port_result = await session.execute(
                    select(PortfolioState).where(PortfolioState.ticker == ticker)
                )
            else:
                port_result = await session.execute(select(PortfolioState).order_by(PortfolioState.market, PortfolioState.ticker))
            positions = port_result.scalars().all()

            cash_result = await session.execute(select(CashBalance))
            cash_balances = cash_result.scalars().all()

        if not positions:
            await msg.edit_text("⚠️ No positions to analyze. Use `/add` first.", parse_mode="Markdown")
            return

        # Build portfolio summary for the agent
        portfolio_data = {
            "cash": {c.currency: float(c.balance) for c in cash_balances},
            "holdings": [
                {"market": p.market, "ticker": p.ticker, "qty": float(p.quantity), "avg_cost": float(p.avg_cost)}
                for p in positions
            ],
        }

        result = await orchestrator.analyze_portfolio(portfolio_data)

        if result.get("error"):
            await msg.edit_text(f"❌ Analysis failed: {result['error']}")
            return

        # Format analysis using <pre> tables
        from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard
        from collections import defaultdict

        lines = ["<b>🧠 PORTFOLIO ANALYSIS</b>"]

        if result.get("portfolio_value"):
            lines.append(f"💰 <b>Value:</b> {result['portfolio_value']:,.2f} | <b>P&amp;L:</b> {result.get('total_unrealized_pnl_pct', 0):+.2f}% | <b>Cash:</b> {result.get('cash_pct', 0):.1f}%")

        # Group holdings by market
        holdings = result.get("holdings", [])
        by_market = defaultdict(list)
        for h in holdings:
            by_market[h.get("market", "UNKNOWN")].append(h)

        market_order = ["IDX", "US", "ETF"]
        rec_emoji_map = {"CUT": "🔴", "TRIM": "🟡", "ADD": "🟢", "HOLD": "⚪️"}

        for market in market_order:
            if market not in by_market:
                continue

            headers = ["Action", "Ticker", "P&L", "AI Reasoning"]
            rows = []
            for h in sorted(by_market[market], key=lambda x: {"CUT": 0, "TRIM": 1, "ADD": 2, "HOLD": 3}.get(x.get("recommendation", "HOLD"), 4)):
                rec = h.get("recommendation", "HOLD")
                emoji = rec_emoji_map.get(rec, "⚪️")
                pnl = h.get("unrealized_pnl_pct", 0)
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                reasoning = escape_html(h.get("reasoning", "")[:80])
                rows.append([f"{emoji} {rec}", h.get("ticker", "?"), f"{pnl_emoji} {pnl:+.1f}%", reasoning])

            table = format_pre_table(headers, rows, align_right=[2])
            lines.append(f"\n📊 <b>{market} MARKET</b>")
            lines.append(f"<pre>{table}</pre>")

        if result.get("top_actions"):
            lines.append("\n━━━━━━━━━━━━━━━━")
            lines.append("📌 <b>Top Actions:</b>")
            for a in result["top_actions"][:3]:
                lines.append(f"<i>• {escape_html(a)}</i>")

        if result.get("portfolio_risks"):
            lines.append("\n⚠️ <b>Portfolio Risks:</b>")
            for r in result["portfolio_risks"][:3]:
                lines.append(f"<i>• {escape_html(r)}</i>")

        # Build keyboard with audit buttons for each ticker
        tickers = [h.get("ticker") for h in holdings]
        keyboard = build_nav_keyboard([
            [(f"🔍 {t}", f"audit_{t}") for t in tickers[:3]],
            [("📊 P&L", "cmd_pnl"), ("☀️ Briefing", "cmd_briefing")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("analyze_cmd_failed", error=str(e), exc_info=True)
        await msg.edit_text("❌ Analysis failed. Check logs for details.")


async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /trades command — show paper trades."""
    if not _is_authorized(update):
        return

    try:
        from src.models.database import async_session
        from src.models.tables import PaperPosition, ClosedPaperTrade
        from sqlalchemy import select, func

        async with async_session() as session:
            # Get open paper positions
            open_result = await session.execute(
                select(PaperPosition).order_by(PaperPosition.entry_date.desc())
            )
            open_positions = open_result.scalars().all()

            # Get closed trades summary
            closed_result = await session.execute(
                select(ClosedPaperTrade).order_by(ClosedPaperTrade.exit_date.desc()).limit(10)
            )
            closed_trades = closed_result.scalars().all()

            # Get P&L summary
            pnl_result = await session.execute(
                select(
                    func.sum(ClosedPaperTrade.realized_pnl).label("total_pnl"),
                    func.count(ClosedPaperTrade.id).label("total_trades")
                )
            )
            pnl_summary = pnl_result.one()

        from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard

        lines = ["<b>📋 PAPER TRADING HISTORY</b>\n"]

        if open_positions:
            lines.append("🟢 <b>OPEN POSITIONS</b>")
            headers = ["Ticker", "Dir", "Entry", "Curr", "P&L"]
            rows = []
            for pos in open_positions:
                emoji = "🟢L" if pos.side == "LONG" else "🔴S"
                pnl = pos.unrealized_pnl_pct or 0
                pnl_str = f"🟢 +{pnl:.1f}%" if pnl >= 0 else f"🔴 {pnl:.1f}%"
                rows.append([pos.ticker, emoji, f"{pos.entry_price:.2f}", f"{pos.current_price or 0:.2f}", pnl_str])
            table = format_pre_table(headers, rows, align_right=[2, 3, 4])
            lines.append(f"<pre>{table}</pre>\n")
        else:
            lines.append("<i>📭 No open positions.</i>\n")

        if closed_trades:
            lines.append("🏁 <b>RECENT CLOSED TRADES</b>")
            headers = ["Ticker", "Result", "P&L", "Reason"]
            rows = []
            for t in closed_trades[:5]:
                pnl = t.realized_pnl_pct or 0
                res = "🟢 Win" if pnl > 0 else "🔴 Loss"
                pnl_str = f"🟢 +{pnl:.1f}%" if pnl >= 0 else f"🔴 {pnl:.1f}%"
                reason = (t.exit_reason or "N/A")[:10]
                rows.append([t.ticker, res, pnl_str, reason])
            table = format_pre_table(headers, rows, align_right=[2])
            lines.append(f"<pre>{table}</pre>")

        if pnl_summary.total_trades:
            total_pnl = pnl_summary.total_pnl or 0
            emoji = "🟢" if total_pnl >= 0 else "🔴"
            lines.append(f"\n━━━━━━━━━━━━━━━━")
            lines.append(f"{emoji} <b>Total Realized P&amp;L:</b> {total_pnl:+,.2f}")
            lines.append(f"<b>Total Trades:</b> {pnl_summary.total_trades}")

        keyboard = build_nav_keyboard([
            [("📊 P&L", "cmd_pnl"), ("☀️ Briefing", "cmd_briefing")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("trades_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ Operation failed. Check logs for details.")


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

        msg = await _reply(update, "📊 <b>Generating Morning Briefing...</b>", parse_mode="HTML")

        # Get both market regimes
        from src.advisory.regime import USRegimeFilter, IDXRegimeFilter
        import asyncio

        us_filter = USRegimeFilter(orchestrator.mcp)
        idx_filter = IDXRegimeFilter(orchestrator.mcp)
        us_regime, idx_regime = await asyncio.gather(
            us_filter.get_current_regime(),
            idx_filter.get_current_regime(),
        )

        # Get portfolio summary
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

        # Calculate portfolio value
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

        lines = [
            "☀️ <b>MORNING BRIEFING</b>",
            f"📅 <i>{datetime.now().strftime('%a, %b %d | %H:%M')}</i>",
            "",
            "🌡️ <b>REGIME &amp; CONTEXT</b>",
            f"<pre>US   : {us_e} {us_regime.get('state', 'UNKNOWN')}"
            f"\n     SPY {us_regime.get('benchmark_price', 'N/A')} | VIX {us_regime.get('vix', 'N/A')}"
            f"\nIDX  : {idx_e} {idx_regime.get('state', 'UNKNOWN')}"
            f"\n     {idx_regime.get('benchmark', 'IHSG')} {idx_regime.get('benchmark_price', 'N/A')}"
            f"\nRec  : {us_regime.get('recommendation', 'N/A')}</pre>",
            "",
            "💼 <b>PORTFOLIO STATUS</b>",
            f"<pre>Total Value : {portfolio_value:,.2f}"
            f"\nCash        : {total_cash:,.2f} ({cash_pct:.1f}%)"
            f"\nPositions   : {len(positions)} open</pre>",
            "",
            "📈 <b>PAPER TRADING</b>",
            f"<pre>Open Trades : {len(paper_positions)}"
            f"\nUnrealized  : {'🟢' if paper_pnl >= 0 else '🔴'} {paper_pnl:+,.2f}</pre>",
        ]

        keyboard = build_nav_keyboard([
            [("🌡️ Deep Regime", "cmd_regime"), ("📈 View P&L", "cmd_pnl")],
            [("📋 Open Trades", "cmd_trades"), ("💼 Portfolio", "cmd_portfolio")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("briefing_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ Briefing failed. Check logs for details.")


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

        await _reply(update, "🌡️ <b>Fetching market regimes...</b>", parse_mode="HTML")

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

        lines = [
            "🌡️ <b>MARKET REGIME</b>\n",

            f"🇺🇸 <b>US MARKET</b>",
            f"<pre>State  : {us_e} {us_regime.get('state', 'UNKNOWN')}"
            f"\nSPY    : {us_regime.get('benchmark_price', 'N/A')} (200 SMA: {us_regime.get('sma200', 'N/A')})"
            f"\nVIX    : {us_regime.get('vix', 'N/A')}"
            f"\nRec    : {us_regime.get('recommendation', 'N/A')}</pre>",
            "",

            f"🇮🇩 <b>IDX MARKET</b>",
            f"<pre>State  : {idx_e} {idx_regime.get('state', 'UNKNOWN')}"
            f"\n{idx_regime.get('benchmark', 'IHSG')}  : {idx_regime.get('benchmark_price', 'N/A')} (200 SMA: {idx_regime.get('sma200', 'N/A')})"
            f"\nBBCA   : {idx_regime.get('bbca_price', 'N/A')}"
            f"\nRec    : {idx_regime.get('recommendation', 'N/A')}</pre>",
        ]

        keyboard = build_nav_keyboard([
            [("☀️ Briefing", "cmd_briefing"), ("📊 P&L", "cmd_pnl")],
            [("💼 Portfolio", "cmd_portfolio")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("regime_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ Regime check failed. Check logs for details.")


async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pnl command — Shadow Portfolio Performance."""
    if not _is_authorized(update):
        return

    try:
        from src.models.database import async_session
        from src.models.tables import PaperPosition, ClosedPaperTrade
        from sqlalchemy import select, func

        async with async_session() as session:
            # Open positions
            open_result = await session.execute(select(PaperPosition))
            open_positions = open_result.scalars().all()

            # Closed trades stats
            closed_result = await session.execute(
                select(
                    func.sum(ClosedPaperTrade.realized_pnl).label("total_pnl"),
                    func.count(ClosedPaperTrade.id).label("total_trades"),
                    func.avg(ClosedPaperTrade.realized_pnl_pct).label("avg_pnl_pct")
                )
            )
            stats = closed_result.one()

            # Win/Loss
            win_result = await session.execute(
                select(func.count(ClosedPaperTrade.id)).where(ClosedPaperTrade.realized_pnl > 0)
            )
            wins = win_result.scalar() or 0

            loss_result = await session.execute(
                select(func.count(ClosedPaperTrade.id)).where(ClosedPaperTrade.realized_pnl <= 0)
            )
            losses = loss_result.scalar() or 0

        total_pnl = stats.total_pnl or 0
        total_trades = stats.total_trades or 0
        avg_pnl_pct = stats.avg_pnl_pct or 0
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        # Open P&L
        open_pnl = sum(float(p.unrealized_pnl or 0) for p in open_positions)
        open_pnl_pct = sum(float(p.unrealized_pnl_pct or 0) for p in open_positions) / len(open_positions) if open_positions else 0

        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

        lines = [
            "📊 <b>SHADOW PORTFOLIO P&amp;L</b>",
            "",
            "🟢 <b>OPEN POSITIONS</b>",
            f"<pre>Count       : {len(open_positions)}"
            f"\nUnrealized  : {'🟢' if open_pnl >= 0 else '🔴'} {open_pnl:+,.2f} ({open_pnl_pct:+.1f}%)</pre>",
            "",
            "🏁 <b>CLOSED TRADES</b>",
            f"<pre>Total       : {total_trades}"
            f"\nWins/Losses : {wins}W / {losses}L"
            f"\nWin Rate    : {win_rate:.1f}%"
            f"\nRealized    : {'🟢' if total_pnl >= 0 else '🔴'} {total_pnl:+,.2f}"
            f"\nAvg P&amp;L     : {avg_pnl_pct:+.1f}%</pre>",
        ]

        keyboard = build_nav_keyboard([
            [("📋 Trades", "cmd_trades"), ("☀️ Briefing", "cmd_briefing")],
            [("📈 Regime", "cmd_regime")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("pnl_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ P&L check failed. Check logs for details.")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /audit <ticker> or /audit portfolio."""
    if not _is_authorized(update):
        return

    if not context.args:
        await _reply(update, "⚠️ Usage:\n`/audit <TICKER>` — audit one signal\n`/audit portfolio` — audit all holdings", parse_mode="Markdown")
        return

    arg = context.args[0].upper()

    try:
        from src.models.database import async_session
        from src.models.tables import Signal, PortfolioState
        from sqlalchemy import select, desc
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard, escape_html, format_pre_table

        # /audit portfolio — latest signal for each portfolio holding
        if arg == "PORTFOLIO":
            msg = await _reply(update, "🔍 <b>Auditing portfolio signals...</b>", parse_mode="HTML")

            async with async_session() as session:
                port_result = await session.execute(
                    select(PortfolioState).order_by(PortfolioState.market, PortfolioState.ticker)
                )
                positions = port_result.scalars().all()

            if not positions:
                await msg.edit_text("📭 No positions to audit. Use `/add` first.", parse_mode="Markdown")
                return

            tickers_by_market: dict[str, list[str]] = {}
            for p in positions:
                tickers_by_market.setdefault(p.market, []).append(p.ticker)

            # Fetch latest signal per ticker
            async with async_session() as session:
                signals: dict[str, Signal] = {}
                for p in positions:
                    result = await session.execute(
                        select(Signal)
                        .where(Signal.ticker == p.ticker, Signal.market == p.market)
                        .order_by(desc(Signal.created_at))
                        .limit(1)
                    )
                    sig = result.scalar_one_or_none()
                    if sig:
                        signals[p.ticker] = sig

            lines = [f"<b>🔍 PORTFOLIO AUDIT</b> — {len(signals)}/{len(positions)} with signals\n"]

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
                lines.append(f"\n📈 <b>{market}</b>")
                lines.append(f"<pre>{table}</pre>")

            # Show reasoning for most recent signal
            if signals:
                latest = max(signals.values(), key=lambda s: s.created_at or datetime.min)
                if latest.reasoning:
                    lines.append(f"\n🧠 <b>Latest signal ({latest.ticker}):</b>")
                    lines.append(f"<i>{escape_html(latest.reasoning[:300])}</i>")

            keyboard = build_nav_keyboard([
                [("🧠 Analyze", "cmd_analyze"), ("🔍 Scan", "cmd_portfolio")],
                [("📊 P&L", "cmd_pnl"), ("☀️ Briefing", "cmd_briefing")],
            ])

            await send_long_message(update, "\n".join(lines), reply_markup=keyboard)
            return

        # /audit <ticker> — single ticker audit
        ticker = arg
        async with async_session() as session:
            result = await session.execute(
                select(Signal)
                .where(Signal.ticker == ticker)
                .order_by(desc(Signal.created_at))
                .limit(1)
            )
            signal = result.scalar_one_or_none()

        if not signal:
            await _reply(update, f"📭 No signal found for <b>{ticker}</b>.\n<i>Run /scan to generate one.</i>", parse_mode="HTML")
            return

        d_emoji = "🟢" if signal.direction == "LONG" else "🔴" if signal.direction == "SHORT" else "⚪️"

        lines = [
            f"🔍 <b>AUDIT LOG: {ticker}</b>",
            f"⏱ <i>Generated: {signal.created_at.strftime('%Y-%m-%d %H:%M') if signal.created_at else 'N/A'}</i>",
            "",
            "📊 <b>SIGNAL METRICS</b>",
            f"<pre>Decision    : {d_emoji} {signal.direction}"
            f"\nStrategy    : {signal.strategy}"
            f"\nConfidence  : {signal.confidence_score}/100"
            f"\nMarket      : {signal.market}</pre>",
        ]

        if signal.entry_price:
            lines.append("")
            lines.append("💰 <b>PRICING</b>")
            entry = f"{signal.entry_price:,.2f}" if signal.entry_price else "N/A"
            target = f"{signal.target_price:,.2f}" if signal.target_price else "N/A"
            sl = f"{signal.stop_loss_price:,.2f}" if signal.stop_loss_price else "N/A"
            rr = f"{signal.risk_reward_ratio:.2f}" if signal.risk_reward_ratio else "N/A"
            lines.append(
                f"<pre>Entry       : {entry}"
                f"\nTarget      : {target}"
                f"\nStop Loss   : {sl}"
                f"\nRisk/Reward : {rr}</pre>"
            )

        if signal.reasoning:
            lines.append("")
            lines.append("🧠 <b>AI REASONING (LLM Synthesis)</b>")
            lines.append(f"<i>{escape_html(signal.reasoning[:500])}</i>")

        keyboard = build_nav_keyboard([
            [("💼 Portfolio", "cmd_portfolio"), ("☀️ Briefing", "cmd_briefing")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("audit_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, "❌ Audit failed. Check logs for details.")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency kill switch — halts all new trading decisions immediately."""
    if not _is_authorized(update):
        return

    operator = update.effective_user.username or str(update.effective_user.id)
    already_active = await emergency.is_active()

    if already_active:
        await _reply(update, "⚠️ Emergency stop is already active.", parse_mode="HTML")
        return

    await emergency.activate(reason="Manual operator halt via Telegram", operator=operator)
    logger.warning("emergency_stop_activated", operator=operator)
    await _reply(update,
        "🚨 <b>EMERGENCY STOP ACTIVATED</b>\n"
        "All new trading decisions are halted.\n"
        "Use /resume to reactivate.",
        parse_mode="HTML",
    )


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume trading after emergency stop."""
    if not _is_authorized(update):
        return

    operator = update.effective_user.username or str(update.effective_user.id)
    is_active = await emergency.is_active()

    if not is_active:
        await _reply(update, "✅ Emergency stop is not active. Trading is normal.", parse_mode="HTML")
        return

    await emergency.deactivate(operator=operator)
    logger.warning("emergency_stop_deactivated", operator=operator)
    await _reply(update,
        "✅ <b>Emergency stop deactivated.</b>\n"
        "Trading decisions can resume.",
        parse_mode="HTML",
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks from inline keyboards."""
    query = update.callback_query
    await query.answer()

    data = query.data

    # Handle audit_ buttons
    if data.startswith("audit_"):
        ticker = data[6:]
        context.args = [ticker]
        await audit_cmd(update, context)
        return

    if not data.startswith("cmd_"):
        return

    cmd = data[4:]  # Remove "cmd_" prefix

    # Route to the appropriate command handler
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
