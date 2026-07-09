"""Karsa Trading System — Crypto UI Formatters

Telegram-specific formatters for position cards, risk buttons, and regime display.
Used by crypto_handlers.py for consistent UX across all screens.
"""

from html import escape
from src.utils.format import HTML, bold, italic, code, fmt


def format_position_card(position: dict, index: int = 0) -> str:
    """Format a single open position as a detailed multi-line card.

    Args:
        position: Dict with keys: symbol, side, size, entry_price, current_price,
                  unrealised_pnl, mark_price, liq_price, stop_loss, take_profit
        index: 1-based position index for display
    Returns:
        HTML-formatted position card string.
    """
    symbol = position.get("symbol", "?")
    side = position.get("side", "?")
    size = float(position.get("size", 0) or 0)
    entry = float(position.get("entry_price", 0) or 0)
    mark = float(position.get("current_price", 0) or 0)
    pnl = float(position.get("unrealised_pnl", 0) or 0)
    liq = float(position.get("liq_price", 0) or 0)
    sl = float(position.get("stop_loss", 0) or 0)
    tp = float(position.get("take_profit", 0) or 0)

    pnl_pct = ((mark - entry) / entry * 100) if side == "Buy" and entry > 0 else (
        ((entry - mark) / entry * 100) if entry > 0 else 0
    )
    pnl_icon = "🟢" if pnl >= 0 else "🔴"
    side_icon = "⬆️" if side == "Buy" else "⬇️"
    side_label = "LONG" if side == "Buy" else "SHORT"

    card = fmt(
        bold(f"{index}. {symbol} ({side_label})"), f" {pnl_icon}", "\n",
        f"┣ Entry: ${entry:,.2f} | Now: ${mark:,.2f}", "\n",
        f"┣ Size: {size} | Liq: ${liq:,.2f}", "\n",
        f"┗ PnL: {pnl_icon} ${pnl:+,.2f} ({pnl_pct:+.2f}%)",
    )

    if sl > 0:
        card = fmt(card, f"\n   SL: ${sl:,.2f}", sep="")
    if tp > 0:
        card = fmt(card, f" | TP: ${tp:,.2f}", sep="")

    return card


def format_risk_button_text(risk_pct: float, wallet_bal: float) -> str:
    """Format risk button text showing percentage and dollar amount.

    Example: "▶️ 30% ($3k)"
    """
    dollar = wallet_bal * (risk_pct / 100)
    if dollar >= 1000:
        dollar_str = f"${dollar / 1000:.1f}k"
    else:
        dollar_str = f"${dollar:,.0f}"
    return f"▶️ {risk_pct:.0f}% ({dollar_str})"


def get_regime_display(regime: str) -> str:
    """Standardize regime output with emoji indicator.

    BULL -> BULL 🟢, BEAR -> BEAR 🔴, NEUTRAL -> NEUTRAL 🟡
    """
    regime = (regime or "UNKNOWN").upper()
    if "BULL" in regime:
        return f"{regime} 🟢"
    elif "BEAR" in regime:
        return f"{regime} 🔴"
    else:
        return f"{regime} 🟡"


def format_tp_alert(symbol: str, side: str, exit_price: float, pnl: float, pnl_pct: float) -> str:
    """Format a Take Profit hit alert message."""
    return fmt(
        bold("🎯 TAKE PROFIT HIT 🎯"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        bold("Symbol: "), f"{symbol} ({side})", "\n",
        bold("Exit Price: "), f"${exit_price:,.2f}", "\n",
        bold("PnL: "), f"🟢 ${pnl:+,.2f} ({pnl_pct:+.2f}%)", "\n\n",
        "Position closed successfully.",
    )


def format_sl_alert(symbol: str, side: str, exit_price: float, pnl: float, pnl_pct: float) -> str:
    """Format a Stop Loss hit alert message."""
    return fmt(
        bold("🛑 STOP LOSS HIT 🛑"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        bold("Symbol: "), f"{symbol} ({side})", "\n",
        bold("Exit Price: "), f"${exit_price:,.2f}", "\n",
        bold("PnL: "), f"🔴 ${pnl:+,.2f} ({pnl_pct:+.2f}%)", "\n\n",
        "Position closed to protect capital.",
    )
