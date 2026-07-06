"""AODE Telegram Command Handlers.

Commands:
  /discover — Latest discovered tokens with scores
  /research <symbol> — Full research report
  /opportunity — Top 10 opportunities by score
  /narrative — Current narrative landscape
  /smartmoney — Recent smart money movements
  /watchlist — Tokens being monitored
  /buckets — Portfolio allocation
"""

from telegram import Update
from telegram.ext import ContextTypes
from src.utils.logging import get_logger

logger = get_logger("aode_handlers")


async def cmd_discover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show latest discovered tokens."""
    from src.models.database import async_session
    from sqlalchemy import text

    try:
        async with async_session() as session:
            result = await session.execute(
                text("""SELECT symbol, chain, source, name, market_cap_usd, price_change_24h_pct, status
                FROM discovered_tokens
                ORDER BY discovered_at DESC LIMIT 15"""),
            )
            rows = result.fetchall()

        if not rows:
            await update.message.reply_text("No tokens discovered yet. Discovery runs hourly.")
            return

        lines = ["<b>🔍 Latest Discoveries</b>\n"]
        for r in rows:
            mcap = f"${float(r[4]):,.0f}" if r[4] else "N/A"
            change = f"{float(r[5]):+.1f}%" if r[5] else ""
            lines.append(f"• <b>{r[0]}</b> ({r[1]}) — {r[3] or ''}")
            lines.append(f"  MCap: {mcap} | 24h: {change} | Source: {r[2]}")
            lines.append(f"  Status: {r[6]}\n")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("cmd_discover_failed", error=str(e))
        await update.message.reply_text("Error fetching discoveries.")


async def cmd_opportunity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top 10 opportunities by composite score."""
    from src.research.research_orchestrator import ResearchOrchestrator

    try:
        orch = ResearchOrchestrator()
        opps = await orch.get_top_opportunities(10)

        if not opps:
            await update.message.reply_text("No scored opportunities yet. Research runs every 4h.")
            return

        lines = ["<b>🏆 Top Opportunities</b>\n"]
        for i, o in enumerate(opps, 1):
            emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(o.get("recommendation"), "⚪")
            lines.append(
                f"{i}. <b>{o['symbol']}</b> — Score: {o['score']:.0f}\n"
                f"   {emoji} {o.get('recommendation', 'N/A')} | Bucket: {o.get('bucket', 'N/A')} | Risk: {o.get('risk', 'N/A')}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("cmd_opportunity_failed", error=str(e))
        await update.message.reply_text("Error fetching opportunities.")


async def cmd_narrative(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current narrative landscape."""
    from src.research.narrative_intel import NarrativeIntelligence

    try:
        intel = NarrativeIntelligence()
        narratives = await intel.detect_narratives()

        if not narratives:
            await update.message.reply_text("No narrative data available.")
            return

        lines = ["<b>📊 Narrative Landscape</b>\n"]
        for n in narratives[:8]:
            momentum_emoji = {"increasing": "📈", "decreasing": "📉", "stable": "➡️"}.get(n.get("momentum"), "")
            lines.append(
                f"• <b>{n['narrative']}</b> — Strength: {n.get('strength', 0):.1f}/10 {momentum_emoji}\n"
                f"  24h: {n.get('market_cap_change_24h_pct', 0):+.1f}%"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("cmd_narrative_failed", error=str(e))
        await update.message.reply_text("Error fetching narratives.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tokens being monitored."""
    from src.research.monitoring_engine import MonitoringEngine

    try:
        engine = MonitoringEngine()
        watched = await engine.get_watched_tokens()

        if not watched:
            await update.message.reply_text("No tokens in watchlist yet.")
            return

        lines = ["<b>👁️ Watchlist</b>\n"]
        for w in watched:
            score = f"{w['score']:.0f}" if w['score'] else "N/A"
            rec = w.get('recommendation') or 'PENDING'
            lines.append(f"• <b>{w['symbol']}</b> ({w.get('chain', '?')}) — Score: {score} | {rec}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("cmd_watchlist_failed", error=str(e))
        await update.message.reply_text("Error fetching watchlist.")


async def cmd_buckets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show portfolio allocation across buckets."""
    from src.research.portfolio_bucker import PortfolioBucker, BUCKET_TARGETS

    try:
        bucker = PortfolioBucker()
        allocation = await bucker.get_current_allocation()

        lines = ["<b>📦 Portfolio Buckets</b>\n"]
        for bucket, target in BUCKET_TARGETS.items():
            current = allocation.get(bucket, {})
            positions = current.get("positions", "[]")
            lines.append(f"<b>{bucket}</b> — Target: {target}%")

            if isinstance(positions, str):
                import json
                try:
                    positions = json.loads(positions)
                except json.JSONDecodeError:
                    positions = []

            if positions:
                for p in positions[:5]:
                    lines.append(f"  • {p.get('symbol', '?')} (score: {p.get('score', 0):.0f})")
            else:
                lines.append("  (empty)")
            lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("cmd_buckets_failed", error=str(e))
        await update.message.reply_text("Error fetching buckets.")


async def cmd_aode_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger research on a specific symbol."""
    if not context.args:
        await update.message.reply_text("Usage: /research <SYMBOL>\nExample: /research ETHUSDT")
        return

    symbol = context.args[0].upper()
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"

    await update.message.reply_text(f"🔍 Researching {symbol}...")

    from src.research.opportunity_scorer import OpportunityScorer
    try:
        scorer = OpportunityScorer()
        result = await scorer.score_opportunity(symbol)
        await scorer.persist_report(result)

        scores = result.get("scores", {})
        lines = [
            f"<b>📊 Research Report: {symbol}</b>\n",
            f"<b>Composite Score: {result['composite_score']:.0f}/100</b>",
            f"Bucket: {result['investment_bucket']} | Risk: {result['risk_category']}",
            f"Confidence: {result['confidence']:.0f}%\n",
            "<b>Scores:</b>",
        ]
        for module, score in scores.items():
            bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
            lines.append(f"  {module}: {bar} {score:.0f}")

        rec = "BUY" if result["composite_score"] >= 70 else ("WATCH" if result["composite_score"] >= 40 else "AVOID")
        emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}[rec]
        lines.append(f"\n{emoji} Recommendation: <b>{rec}</b>")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("cmd_research_failed", symbol=symbol, error=str(e))
        await update.message.reply_text(f"Error researching {symbol}.")


async def cmd_aode_smartmoney(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent smart money movements."""
    from src.models.database import async_session
    from sqlalchemy import text

    try:
        async with async_session() as session:
            result = await session.execute(
                text("""SELECT token_symbol, action, details, created_at
                FROM research_audit_log
                WHERE action = 'smart_money_scan'
                ORDER BY created_at DESC LIMIT 10"""),
            )
            rows = result.fetchall()

        if not rows:
            await update.message.reply_text("No smart money data yet.")
            return

        lines = ["<b>🐋 Smart Money Activity</b>\n"]
        for r in rows:
            lines.append(f"• <b>{r[0]}</b> — {r[2][:100] if r[2] else 'N/A'}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("cmd_smartmoney_failed", error=str(e))
        await update.message.reply_text("Error fetching smart money data.")
