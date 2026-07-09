"""Karsa Trading System — Daily Digest

Sends a daily Telegram summary at 00:00 UTC with:
- Session return %
- Win rate
- Open positions count
- Daily PnL
- Top/bottom performers
"""

import asyncio
from datetime import datetime, timezone

import httpx

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("daily_digest")


async def send_daily_digest(redis_client, chat_id: int | None = None) -> None:
    """Send daily PnL digest via Telegram.

    Args:
        redis_client: Redis client for reading session data
        chat_id: Telegram chat ID. If None, reads from Redis.
    """
    if not chat_id:
        try:
            chat_id = await redis_client.get("karsa:telegram_chat_id")
            if not chat_id:
                logger.warning("daily_digest_no_chat_id")
                return
            chat_id = int(chat_id)
        except Exception as e:
            logger.error("daily_digest_chat_id_failed", error=str(e))
            return

    token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
    if not token:
        logger.warning("daily_digest_no_token")
        return

    try:
        # Gather metrics from Redis
        start_equity = float(await redis_client.get("karsa:session_start_equity") or 0)
        current_equity = float(await redis_client.get("karsa:session_current_equity") or 0)
        realized_pnl = float(await redis_client.get("karsa:session_realized_pnl") or 0)
        unrealized_pnl = float(await redis_client.get("karsa:session_unrealized_pnl") or 0)
        total_trades = int(await redis_client.get("karsa:session_total_trades") or 0)
        winning_trades = int(await redis_client.get("karsa:session_winning_trades") or 0)
        open_positions = int(await redis_client.get("karsa:open_positions_count") or 0)

        # Calculate derived metrics
        total_pnl = realized_pnl + unrealized_pnl
        return_pct = ((current_equity - start_equity) / start_equity * 100) if start_equity > 0 else 0
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        # Get top/bottom performers from Redis sorted sets
        top_performers = []
        bottom_performers = []
        try:
            top = await redis_client.zrevrange("karsa:position_pnl", 0, 2, withscores=True)
            bottom = await redis_client.zrange("karsa:position_pnl", 0, 2, withscores=True)
            top_performers = [(t[0].decode() if isinstance(t[0], bytes) else t[0], t[1]) for t in (top or [])]
            bottom_performers = [(t[0].decode() if isinstance(t[0], bytes) else t[0], t[1]) for t in (bottom or [])]
        except Exception:
            pass

        # Build message
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"📊 <b>Daily Digest — {date_str}</b>",
            "",
            f"💰 <b>Equity:</b> ${current_equity:,.2f} ({return_pct:+.2f}%)",
            f"📈 <b>Realized PnL:</b> ${realized_pnl:,.2f}",
            f"📉 <b>Unrealized PnL:</b> ${unrealized_pnl:,.2f}",
            f"💵 <b>Total PnL:</b> ${total_pnl:,.2f}",
            "",
            f"🎯 <b>Win Rate:</b> {win_rate:.1f}% ({winning_trades}/{total_trades})",
            f"📂 <b>Open Positions:</b> {open_positions}",
        ]

        if top_performers:
            lines.append("")
            lines.append("🏆 <b>Top Performers:</b>")
            for ticker, pnl in top_performers:
                lines.append(f"  • {ticker}: ${pnl:+,.2f}")

        if bottom_performers:
            lines.append("")
            lines.append("⚠️ <b>Bottom Performers:</b>")
            for ticker, pnl in bottom_performers:
                lines.append(f"  • {ticker}: ${pnl:+,.2f}")

        message = "\n".join(lines)

        # Send via Telegram
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
            if resp.status_code == 200:
                logger.info("daily_digest_sent")
            else:
                logger.warning("daily_digest_send_failed", status=resp.status_code)

    except Exception as e:
        logger.error("daily_digest_failed", error=str(e))
