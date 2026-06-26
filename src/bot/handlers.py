"""Karsa Trading System - Telegram Bot Command Handlers"""

from decimal import Decimal, InvalidOperation
from telegram import Update
from telegram.ext import ContextTypes
import httpx

from src.config import settings, LLM_BASE_URL
from src.utils.logging import get_logger

logger = get_logger("telegram_handlers")


def parse_decimal(raw: str) -> Decimal:
    """Parse number string handling both comma and dot as decimal separator.

    Rules:
    - Contains both . and , → last one is decimal separator (1,234.56 or 1.234,56)
    - Contains only , → decimal separator (0,006421695)
    - Contains only . → decimal separator (0.006421695)
    - Neither → integer
    """
    raw = raw.strip()
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    return Decimal(raw)


def _is_authorized(update: Update) -> bool:
    """Check if message is from authorized chat."""
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if settings.TELEGRAM_CHAT_ID and chat_id != str(settings.TELEGRAM_CHAT_ID):
        logger.warning("unauthorized_chat", chat_id=chat_id)
        return False
    return True


async def _reply(update: Update, text: str, add_timestamp: bool = True, **kwargs):
    """Reply to message or edit callback query message."""
    if add_timestamp:
        from datetime import datetime
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
        "<b>Portfolio Management:</b>\n"
        "/portfolio - View full portfolio &amp; cash\n"
        "/add &lt;market&gt; &lt;ticker&gt; &lt;qty&gt; &lt;price&gt; - Add position\n"
        "/add cash &lt;currency&gt; &lt;amount&gt; - Set cash balance\n"
        "/remove &lt;market&gt; &lt;ticker&gt; - Remove position\n"
        "/edit &lt;market&gt; &lt;ticker&gt; qty|price &lt;value&gt; - Edit position\n"
        "/edit cash &lt;currency&gt; &lt;amount&gt; - Edit cash balance\n\n"
        "<b>Advisory &amp; Analysis:</b>\n"
        "/analyze - Analyze entire portfolio vs market\n"
        "/analyze &lt;ticker&gt; - Deep dive on single holding\n"
        "/scan &lt;market&gt; &lt;ticker&gt; - Quick market readout\n"
        "/audit &lt;ticker&gt; - AI reasoning &amp; risk check\n\n"
        "<b>CIO Dashboard:</b>\n"
        "/briefing - Morning dashboard &amp; regime\n"
        "/regime - Current market state\n"
        "/pnl - Shadow portfolio performance\n"
        "/trades - Paper trading history\n\n"
        "<b>System:</b>\n"
        "/status - System status &amp; scheduler\n",
        parse_mode="HTML",
    )


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan <market> <ticker> command."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 2:
        await _reply(update, "⚠️ Usage: `/scan <market> <ticker>`\nExample: `/scan IDX BBCA`", parse_mode="Markdown")
        return

    market, ticker = context.args[0].upper(), context.args[1].upper()
    msg = await _reply(update, f"🔍 Scanning {ticker} ({market})...")

    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await msg.edit_text("⚠️ System error: Orchestrator not connected.")
        return

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
        await msg.edit_text(f"❌ Scan error: {str(e)}")


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
        import redis.asyncio as redis
        r = redis.from_url(settings.REDIS_URL)
        redis_ok = await r.ping()
        await r.close()
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
        orchestrator_url = "http://karsa-orchestrator:8080"
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

    # Check Kill Switch (Redis flag)
    kill_switch_status = "🟢 Kill Switch (Inactive)"
    try:
        import redis.asyncio as redis
        r = redis.from_url(settings.REDIS_URL)
        halt = await r.get("HALT_TRADING")
        await r.close()
        if halt:
            kill_switch_status = "🔴 Kill Switch (ACTIVE - Trading Halted)"
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
            positions = port_result.scalars().all()

            cash_result = await session.execute(select(CashBalance))
            cash_balances = cash_result.scalars().all()
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

            async with async_session() as session:
                result = await session.execute(select(CashBalance).where(CashBalance.currency == currency))
                cash = result.scalar_one_or_none()
                if cash:
                    cash.balance = amount
                else:
                    session.add(CashBalance(currency=currency, balance=amount))
                await session.commit()
            await _reply(update,f"✅ Cash balance set: {amount:,.2f} {currency}", parse_mode="Markdown")
        except Exception as e:
            logger.error("add_cash_failed", error=str(e))
            await _reply(update,f"❌ Error: {str(e)}")
        return

    # /add IDX BBCA 500 8500
    if len(args) < 4:
        await _reply(update,"⚠️ Usage: `/add IDX BBCA 500 8500`", parse_mode="Markdown")
        return

    market = args[0].upper()
    ticker = args[1].upper()
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
        await _reply(update,
            f"✅ Added: *{ticker}* ({market}) — {qty} @ {price}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("add_position_failed", error=str(e))
        await _reply(update,f"❌ Error: {str(e)}")


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
        await _reply(update,f"❌ Error: {str(e)}")


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
            await _reply(update,f"❌ Error: {str(e)}")
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
        await _reply(update,f"❌ Error: {str(e)}")


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
        from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard, escape_html
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
        await msg.edit_text(f"❌ Analysis error: {str(e)}")


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
        await _reply(update,f"❌ Error: {str(e)}")


async def briefing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /briefing command — Morning Dashboard."""
    if not _is_authorized(update):
        return

    try:
        from datetime import datetime
        from src.advisory.regime import MacroRegimeFilter
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

        orchestrator = context.bot_data.get("orchestrator")
        if not orchestrator:
            await _reply(update, "⚠️ System error: Orchestrator not connected.")
            return

        msg = await _reply(update, "📊 <b>Generating Morning Briefing...</b>", parse_mode="HTML")

        # Get market regime
        regime_filter = MacroRegimeFilter(orchestrator.mcp)
        regime = await regime_filter.get_current_regime()

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

        # Regime emoji
        regime_state = regime.get("state", "UNKNOWN")
        regime_emoji = "🟢" if regime_state == "BULL" else "🔴" if regime_state == "BEAR" else "🟡"

        lines = [
            "☀️ <b>MORNING BRIEFING</b>",
            f"📅 <i>{datetime.now().strftime('%a, %b %d | %H:%M')}</i>",
            "",
            f"🌡️ <b>REGIME &amp; CONTEXT</b>",
            f"<pre>Vibe        : {regime_emoji} {regime_state}"
            f"\nVIX         : {regime.get('vix', 'N/A')}"
            f"\nSPY         : {regime.get('spy_price', 'N/A')} (200 SMA: {regime.get('spy_sma200', 'N/A')})"
            f"\nRec         : {regime.get('recommendation', 'N/A')}</pre>",
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
        await _reply(update, f"❌ Briefing error: {str(e)}")


async def regime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /regime command — Market State."""
    if not _is_authorized(update):
        return

    try:
        from src.advisory.regime import MacroRegimeFilter

        orchestrator = context.bot_data.get("orchestrator")
        if not orchestrator:
            await _reply(update,"⚠️ System error: Orchestrator not connected.")
            return

        regime_filter = MacroRegimeFilter(orchestrator.mcp)
        regime = await regime_filter.get_current_regime()

        regime_state = regime.get("state", "UNKNOWN")
        emoji = "🟢" if regime_state == "BULL" else "🔴" if regime_state == "BEAR" else "🟡"

        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

        lines = [
            "🌡️ <b>MARKET REGIME</b>",
            f"<pre>State       : {emoji} {regime_state}"
            f"\nVIX         : {regime.get('vix', 'N/A')}"
            f"\nSPY         : {regime.get('spy_price', 'N/A')} (200 SMA: {regime.get('spy_sma200', 'N/A')})"
            f"\nRec         : {regime.get('recommendation', 'N/A')}</pre>"
        ]

        keyboard = build_nav_keyboard([
            [("☀️ Briefing", "cmd_briefing"), ("📊 P&L", "cmd_pnl")],
            [("💼 Portfolio", "cmd_portfolio")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("regime_cmd_failed", error=str(e), exc_info=True)
        await _reply(update,f"❌ Regime error: {str(e)}")


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
        await _reply(update,f"❌ P&L error: {str(e)}")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /audit <ticker> command — show AI reasoning for a signal."""
    if not _is_authorized(update):
        return

    ticker = context.args[0].upper() if context.args else None
    if not ticker:
        await _reply(update, "⚠️ Usage: `/audit <TICKER>`\nExample: `/audit NVDA`", parse_mode="Markdown")
        return

    try:
        from src.models.database import async_session
        from src.models.tables import Signal
        from sqlalchemy import select, desc
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard, escape_html

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

        emoji = "🟢" if signal.direction == "LONG" else "🔴" if signal.direction == "SHORT" else "⚪️"

        lines = [
            f"🔍 <b>AUDIT LOG: {ticker}</b>",
            f"⏱ <i>Generated: {signal.created_at.strftime('%Y-%m-%d %H:%M') if signal.created_at else 'N/A'}</i>",
            "",
            "📊 <b>SIGNAL METRICS</b>",
            f"<pre>Decision    : {emoji} {signal.direction}"
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
            [("💡 Ideas", "cmd_ideas"), ("☀️ Briefing", "cmd_briefing")],
            [("💼 Portfolio", "cmd_portfolio")],
        ])

        await send_long_message(update, "\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.error("audit_cmd_failed", error=str(e), exc_info=True)
        await _reply(update, f"❌ Audit error: {str(e)}")


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
