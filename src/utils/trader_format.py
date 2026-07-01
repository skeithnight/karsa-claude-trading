"""src/utils/trader_format.py — Rich Telegram Formatters for the Crypto Trader Persona"""
from src.utils.format import HTML, bold, italic, fmt, code

def funding_gauge(rate: float) -> HTML:
    """Renders a visually appealing funding gauge with directional color coding."""
    rate_pct = rate * 100
    abs_rate = abs(rate_pct)
    # Scale from 0 to 0.05% for gauge fill (e.g. 5 steps of 0.01%)
    filled_blocks = min(5, max(1, int(abs_rate / 0.01)))
    bar = "█" * filled_blocks + "░" * (5 - filled_blocks)
    
    # Positive funding = longs pay shorts (red flag for longs, crowded long)
    # Negative funding = shorts pay longs (green flag for longs, crowded short)
    color_emoji = "🟢" if rate < 0 else "🔴" if rate > 0 else "⚪️"
    return HTML(f"{color_emoji} <code>{bar}</code> {rate_pct:+.4f}%")

def regime_banner(regime_state: str, hurst: float, adx: float, recommendation: str) -> HTML:
    """Renders institutional-grade regime status banner."""
    emoji = {"TREND_BULL": "🟢", "TREND_BEAR": "🔴", "MEAN_REVERSION": "🟡", "CHOP": "⚪️"}.get(regime_state, "⚪️")
    return fmt(
        bold(f"{emoji} REGIME: {regime_state}"), "\n",
        f"├ Hurst Exponent: {hurst} | ADX: {adx}\n",
        f"└ ", italic(recommendation)
    )

def signal_card(ticker: str, direction: str, confidence: float, entry: float, sl: float, tp: float, reasoning: str) -> HTML:
    """Renders a beautiful trade alert card for execution notifications."""
    emoji = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪️"
    rr = "N/A"
    if entry and sl and tp:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk > 0:
            rr = f"{reward / risk:.2f}:1"
            
    filled = min(10, max(1, int(confidence / 10)))
    conf_bar = "█" * filled + "░" * (10 - filled)
    
    return fmt(
        f"{emoji} ", bold(f"{direction} INITIATED — {ticker}"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        f"📍 Entry   : ${entry:,.4f}\n" if entry else "",
        f"🛑 Stop    : ${sl:,.4f}\n" if sl else "",
        f"🎯 Target  : ${tp:,.4f} (R/R: {rr})\n" if tp else "",
        f"📊 Conf    : <code>{conf_bar}</code> {confidence:.0f}%\n",
        "────────────────────────────────\n",
        bold("💡 Thesis:"), "\n", italic(reasoning)
    )

def perf_dashboard(win_rate: float, avg_win: float, avg_loss: float, total_pnl: float, total_trades: int) -> HTML:
    """Renders performance statistics with equity curve summary representation."""
    trend_emoji = "📈" if total_pnl >= 0 else "📉"
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    
    filled = min(10, max(1, int(win_rate / 10))) if win_rate else 0
    wr_bar = "█" * filled + "░" * (10 - filled)
    
    return fmt(
        bold(f"{trend_emoji} PERFORMANCE DASHBOARD"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        bold("Summary:"), "\n",
        f"├ Win Rate  : <code>{wr_bar}</code> {win_rate:.1f}%\n",
        f"├ Realized  : {pnl_emoji} ${total_pnl:+,.2f}\n",
        f"├ Total Ops : {total_trades} trades closed\n",
        f"└ Win/Loss  : Avg Win +{avg_win:.2f}% | Avg Loss {avg_loss:.2f}%\n"
    )

def market_snapshot_card(ticker: str, quote: dict, ta: dict, funding: float, oi: float) -> HTML:
    """Renders deep technical and sentiment snapshot of a single instrument."""
    price = quote.get("last_price", 0.0)
    change_24h = quote.get("change_24h_pct", 0.0)
    chg_emoji = "🔺" if change_24h >= 0 else "🔻"
    
    rsi = ta.get("rsi", {}).get("rsi", 50.0)
    rsi_sig = ta.get("rsi", {}).get("signal", "neutral")
    bb_sig = ta.get("bollinger", {}).get("signal", "within_bands")
    macd_sig = ta.get("macd", {}).get("crossover", "neutral")
    ema20 = ta.get("ema_20", {}).get("ema", 0.0)
    ema50 = ta.get("ema_50", {}).get("ema", 0.0)
    
    trend = "BULLISH" if price > ema20 > ema50 else "BEARISH" if price < ema20 < ema50 else "CHOP/NEUTRAL"
    trend_emoji = "🟢" if trend == "BULLISH" else "🔴" if trend == "BEARISH" else "⚪️"
    
    return fmt(
        bold(f"🖥️ SNAPSHOT: {ticker}"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        f"📍 Price    : ${price:,.4f} ({chg_emoji} {change_24h:+.2f}%)\n",
        f"🔥 Funding  : ", funding_gauge(funding), "\n",
        f"📊 Open Int : ${oi:,.0f}\n" if oi else "",
        f"🌡️ Trend    : {trend_emoji} {trend}\n",
        bold("📝 Technical Indicators:"), "\n",
        f"  • RSI(14) : {rsi:.1f} ({rsi_sig.upper()})\n",
        f"  • Bollinger: {bb_sig.upper()}\n",
        f"  • MACD     : {macd_sig.upper()}\n",
        f"  • EMA      : Price vs 20EMA ({'ABOVE' if price > ema20 else 'BELOW'})\n"
    )

def briefing_block(regime: dict, top_movers: list, funding_alerts: list) -> HTML:
    """Assembles full trading desk briefing statement."""
    state = regime.get("state", "UNKNOWN")
    hurst = regime.get("hurst", "N/A")
    adx = regime.get("adx", "N/A")
    recommendation = regime.get("recommendation", "")
    btc_price = regime.get("benchmark_price", "N/A")
    btc_dom = regime.get("btc_dominance", "N/A")
    season = regime.get("market_season", "UNKNOWN")
    
    regime_emoji = {"TREND_BULL": "🟢", "TREND_BEAR": "🔴", "MEAN_REVERSION": "🟡", "CHOP": "⚪️"}.get(state, "⚪️")
    
    movers_lines = []
    for m in top_movers:
        symbol = m.get("symbol", "?")
        chg = m.get("change_24h_pct", 0.0)
        e = "🔺" if chg >= 0 else "🔻"
        movers_lines.append(f"  • {symbol:<10} ${m.get('last_price', 0.0):,.4f} ({e} {chg:+.2f}%)")
    movers_str = "\n".join(movers_lines) if movers_lines else "  No significant moves."
    
    funding_lines = []
    for f in funding_alerts:
        symbol = f.get("symbol", "?")
        rate = f.get("funding_rate", 0.0)
        funding_lines.append(f"  • {symbol:<10} {rate*100:+.4f}% (annual {rate*100*3*365:+.0f}%)")
    funding_str = "\n".join(funding_lines) if funding_lines else "  No funding extremes."
    
    return fmt(
        bold("🌐 COIN DESK BRIEFING"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("🌡️ Market Regime:"), "\n",
        f"  State     : {regime_emoji} {state}\n",
        f"  BTC Price : ${btc_price:,.2f}\n" if isinstance(btc_price, (int, float)) else f"  BTC Price : {btc_price}\n",
        f"  BTC Dom   : {btc_dom}% (Season: {season})\n",
        f"  Metrics   : Hurst {hurst} | ADX {adx}\n",
        f"  Tactics   : ", italic(recommendation), "\n\n",
        bold("🚀 Top Universe Movers:"), "\n",
        movers_str, "\n\n",
        bold("🔥 Funding Alerts (Position Crowding):"), "\n",
        funding_str, "\n"
    )
