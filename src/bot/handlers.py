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


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "🤖 *Karsa Portfolio Analyst*\n\n"
        "Commands:\n"
        "/portfolio - View full portfolio & cash\n"
        "/add <market> <ticker> <qty> <price> - Add position\n"
        "/add cash <currency> <amount> - Set cash balance\n"
        "/remove <market> <ticker> - Remove position\n"
        "/edit <market> <ticker> qty|price <value> - Edit position\n"
        "/edit cash <currency> <amount> - Edit cash balance\n"
        "/analyze - Analyze entire portfolio vs market\n"
        "/analyze <ticker> - Deep dive on single holding\n"
        "/scan <market> <ticker> - Quick market readout\n"
        "/status - System status\n",
        parse_mode="Markdown",
    )


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan <market> <ticker> command."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: `/scan <market> <ticker>`\nExample: `/scan IDX BBCA`", parse_mode="Markdown")
        return

    market, ticker = context.args[0].upper(), context.args[1].upper()
    msg = await update.message.reply_text(f"🔍 Scanning {ticker} ({market})...")

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
            f"ℹ️ *Scan: {ticker} ({market})*\n"
            f"Strategy: {result.get('strategy', 'Unknown')}\n"
            f"Confidence: {result.get('confidence_score', 0)}/100\n"
            f"Direction: {result.get('direction', 'N/A')}\n\n"
            f"📝 *Reasoning:*\n{result.get('reasoning', 'No reasoning provided.')}"
        )
        await msg.edit_text(text, parse_mode="Markdown")
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

    await update.message.reply_text(
        "📊 *System Status*\n━━━━━━━━━━━━━━━━\n"
        f"{'🟢' if db_ok else '🔴'} PostgreSQL\n"
        f"{'🟢' if redis_ok else '🔴'} Redis\n"
        f"{router_status}\n"
        "🟢 Orchestrator\n",
        parse_mode="Markdown",
    )


async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View full portfolio & cash."""
    if not _is_authorized(update):
        return
    from src.models.database import async_session
    from src.models.tables import PortfolioState, CashBalance
    from sqlalchemy import select

    try:
        async with async_session() as session:
            port_result = await session.execute(select(PortfolioState).order_by(PortfolioState.market, PortfolioState.ticker))
            positions = port_result.scalars().all()

            cash_result = await session.execute(select(CashBalance))
            cash_balances = cash_result.scalars().all()
    except Exception as e:
        logger.error("portfolio_db_error", error=str(e), exc_info=True)
        await update.message.reply_text(f"❌ Database error: {str(e)}", parse_mode="Markdown")
        return

    lines = ["💼 *Portfolio*\n━━━━━━━━━━━━━━━━"]

    # Cash
    for cash in cash_balances:
        lines.append(f"💵 *Cash*: {cash.balance:,.2f} {cash.currency}")

    if cash_balances:
        lines.append("━━━━━━━━━━━━━━━━")

    # Positions
    if not positions:
        lines.append("No positions open.")
    else:
        # Group by market
        from itertools import groupby
        for market, market_positions in groupby(positions, key=lambda p: p.market):
            lines.append(f"\n📈 *{market} Market*")
            for p in market_positions:
                pnl = ""
                if p.unrealized_pnl and p.unrealized_pnl != 0:
                    emoji = "🟢" if p.unrealized_pnl > 0 else "🔴"
                    pnl = f" {emoji} {p.unrealized_pnl:,.0f}"
                lines.append(f"*{p.ticker}* — {p.quantity:,.4f} @ {p.avg_cost:,.2f}{pnl}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add <market> <ticker> <qty> <price> or /add cash <currency> <amount>."""
    if not _is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text(
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
            await update.message.reply_text("⚠️ Usage: `/add cash IDR 50000000`", parse_mode="Markdown")
            return
        currency = args[1].upper()
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
            await update.message.reply_text(f"✅ Cash balance set: {amount:,.2f} {currency}", parse_mode="Markdown")
        except Exception as e:
            logger.error("add_cash_failed", error=str(e))
            await update.message.reply_text(f"❌ Error: {str(e)}")
        return

    # /add IDX BBCA 500 8500
    if len(args) < 4:
        await update.message.reply_text("⚠️ Usage: `/add IDX BBCA 500 8500`", parse_mode="Markdown")
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
                await update.message.reply_text(
                    f"⚠️ {ticker} ({market}) already exists. Use `/edit` to update.", parse_mode="Markdown"
                )
                return
            session.add(PortfolioState(market=market, ticker=ticker, quantity=qty, avg_cost=price))
            await session.commit()
        await update.message.reply_text(
            f"✅ Added: *{ticker}* ({market}) — {qty} @ {price}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("add_position_failed", error=str(e))
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove <market> <ticker>."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: `/remove <market> <ticker>`\nExample: `/remove IDX BBCA`", parse_mode="Markdown")
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
                await update.message.reply_text(f"⚠️ {ticker} ({market}) not found in portfolio.", parse_mode="Markdown")
                return
            await session.delete(pos)
            await session.commit()
        await update.message.reply_text(f"✅ Removed: *{ticker}* ({market})", parse_mode="Markdown")
    except Exception as e:
        logger.error("remove_position_failed", error=str(e))
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit <market> <ticker> qty|price <value> or /edit cash <currency> <amount>."""
    if not _is_authorized(update):
        return
    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
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
            await update.message.reply_text("⚠️ Usage: `/edit cash IDR 50000000`", parse_mode="Markdown")
            return
        currency = args[1].upper()
        amount = parse_decimal(args[2])
        try:
            from src.models.database import async_session
            from src.models.tables import CashBalance
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(select(CashBalance).where(CashBalance.currency == currency))
                cash = result.scalar_one_or_none()
                if not cash:
                    await update.message.reply_text(f"⚠️ No cash balance found for {currency}. Use `/add cash {currency} <amount>`.", parse_mode="Markdown")
                    return
                cash.balance = amount
                await session.commit()
            await update.message.reply_text(f"✅ Cash balance updated: {amount:,.2f} {currency}", parse_mode="Markdown")
        except Exception as e:
            logger.error("edit_cash_failed", error=str(e))
            await update.message.reply_text(f"❌ Error: {str(e)}")
        return

    # /edit IDX BBCA qty 600
    if len(args) < 4:
        await update.message.reply_text(
            "⚠️ Usage: `/edit <market> <ticker> qty|price <value>`\nExample: `/edit IDX BBCA qty 600`",
            parse_mode="Markdown"
        )
        return

    market, ticker = args[0].upper(), args[1].upper()
    field = args[2].lower()
    value = parse_decimal(args[3])

    if field not in ("qty", "quantity", "price", "avg_cost"):
        await update.message.reply_text("⚠️ Field must be `qty` or `price`.", parse_mode="Markdown")
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
                await update.message.reply_text(f"⚠️ {ticker} ({market}) not found.", parse_mode="Markdown")
                return
            if field in ("qty", "quantity"):
                pos.quantity = value
            else:
                pos.avg_cost = value
            await session.commit()
        await update.message.reply_text(
            f"✅ Updated: *{ticker}* ({market}) — {field} = {value}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("edit_position_failed", error=str(e))
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyze or /analyze <ticker> — run portfolio analysis via LLM."""
    if not _is_authorized(update):
        return

    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await update.message.reply_text("⚠️ System error: Orchestrator not connected.")
        return

    ticker = context.args[0].upper() if context.args else None
    msg_text = f"🧠 Analyzing {'*' + ticker + '*' if ticker else 'portfolio'}..."
    msg = await update.message.reply_text(msg_text, parse_mode="Markdown")

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

        # Format analysis
        lines = [f"🧠 *Portfolio Analysis*\n━━━━━━━━━━━━━━━━"]

        if result.get("portfolio_value"):
            lines.append(f"💰 Value: {result['portfolio_value']:,.2f}")
        if result.get("total_unrealized_pnl_pct") is not None:
            emoji = "🟢" if result["total_unrealized_pnl_pct"] >= 0 else "🔴"
            lines.append(f"{emoji} P&L: {result['total_unrealized_pnl_pct']:+.2f}%")
        if result.get("cash_pct") is not None:
            lines.append(f"💵 Cash: {result['cash_pct']:.1f}%")

        lines.append("━━━━━━━━━━━━━━━━")

        for h in result.get("holdings", []):
            emoji = "🟢" if h.get("unrealized_pnl_pct", 0) >= 0 else "🔴"
            lines.append(
                f"*{h.get('ticker', '?')}* ({h.get('market', '?')})\n"
                f"  {emoji} {h.get('unrealized_pnl_pct', 0):+.1f}% | "
                f"Rec: {h.get('recommendation', 'HOLD')}\n"
                f"  {h.get('reasoning', '')[:150]}"
            )

        if result.get("top_actions"):
            lines.append("\n📌 *Top Actions:*")
            for a in result["top_actions"][:3]:
                lines.append(f"  • {a}")

        if result.get("portfolio_risks"):
            lines.append("\n⚠️ *Risks:*")
            for r in result["portfolio_risks"][:3]:
                lines.append(f"  • {r}")

        await msg.edit_text("\n".join(lines)[:4000], parse_mode="Markdown")

    except Exception as e:
        logger.error("analyze_cmd_failed", error=str(e), exc_info=True)
        await msg.edit_text(f"❌ Analysis error: {str(e)}")


async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /trades command — placeholder since trade execution removed."""
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "📋 *Trade History*\n━━━━━━━━━━━━━━━━\nTrade execution removed. Karsa is now a portfolio tracker & analyst.",
        parse_mode="Markdown",
    )
