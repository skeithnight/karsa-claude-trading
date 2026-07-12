"""Meme Coin Sniper — lightweight momentum scanner (experiment).

No LLM. Pure TA signals on high-gainers:
  1h price change > +8% AND 15m price change > +2%
  15m volume > 3x 4h rolling median volume
  Liquidity depth >= $250k
  Funding booster: negative funding → 1.5x size
"""
import statistics
import time
from datetime import datetime, timezone
from src.utils.logging import get_logger

logger = get_logger("meme_sniper")


async def scan(bybit_client, settings) -> list[dict]:
    """Scan all USDT perps for momentum/gainer setups."""
    min_1h = settings.MEME_1H_MIN_CHANGE_PCT
    min_15m = settings.MEME_15M_MIN_CHANGE_PCT
    vol_mult = settings.MEME_VOL_SPIKE_MULT
    min_liq = settings.MEME_MIN_LIQUIDITY_USD

    # 1. Fetch all USDT perps
    perps = await bybit_client.get_all_perps(min_volume_usd=0)
    if not perps:
        return []

    signals = []
    for p in perps:
        sym = p.get("symbol", "")
        if not sym.endswith("USDT"):
            continue

        try:
            # 2. Check price momentum (1h and 15m)
            klines_1h = await bybit_client.get_ohlcv(sym, interval="60", limit=2)
            if len(klines_1h) < 2:
                continue
            price_1h_ago = float(klines_1h[0].get("close", 0))
            price_now = float(klines_1h[-1].get("close", 0))
            if price_1h_ago <= 0:
                continue
            change_1h_pct = ((price_now - price_1h_ago) / price_1h_ago) * 100

            klines_15m = await bybit_client.get_ohlcv(sym, interval="15", limit=2)
            if len(klines_15m) < 2:
                continue
            price_15m_ago = float(klines_15m[0].get("close", 0))
            if price_15m_ago <= 0:
                continue
            change_15m_pct = ((price_now - price_15m_ago) / price_15m_ago) * 100

            if change_1h_pct < min_1h or change_15m_pct < min_15m:
                continue

            # 3. Volume spike: 15m vol > Nx 4h rolling median
            klines_4h = await bybit_client.get_ohlcv(sym, interval="240", limit=10)
            if len(klines_4h) < 4:
                continue
            # exclude first 2 candles (recent listing noise)
            vols_4h = [float(k.get("volume", 0)) for k in klines_4h[2:]]
            if not vols_4h or statistics.median(vols_4h) <= 0:
                continue
            vol_median_4h = statistics.median(vols_4h)
            vol_15m = float(klines_15m[-1].get("volume", 0))

            if vol_15m < vol_median_4h * vol_mult:
                continue

            # 4. Liquidity depth check
            depth_usd = await bybit_client.get_order_book_depth_usd(sym, depth=10)
            if depth_usd < min_liq:
                continue

            # 5. Funding booster
            funding_info = await bybit_client.get_funding_rate(sym)
            funding_rate = float(funding_info.get("funding_rate", 0))
            size_mult = 1.0
            if funding_rate < -0.0001:  # negative funding
                size_mult = 1.5

            # 6. Entry price and stop-loss
            atr = _estimate_atr(klines_15m)
            stop_loss = price_now - (atr * 1.5) if atr > 0 else price_now * 0.97
            take_profit = price_now * 1.15  # +15% TP1

            signal = {
                "ticker": sym,
                "market": "CRYPTO",
                "direction": "LONG",
                "confidence_score": _calc_confidence(change_1h_pct, change_15m_pct, vol_15m, vol_median_4h),
                "entry_price": price_now,
                "target_price": take_profit,
                "stop_loss_price": stop_loss,
                "tif": "4h",
                "reasoning": f"Momentum: 1h {change_1h_pct:+.1f}%, 15m {change_15m_pct:+.1f}%, vol spike {vol_15m/vol_median_4h:.1f}x, depth ${depth_usd:,.0f}, funding {funding_rate*100:.4f}%",
                "_signal_source": "meme_sniper",
                "_size_multiplier": size_mult,
                "_meme_meta": {
                    "change_1h": round(change_1h_pct, 2),
                    "change_15m": round(change_15m_pct, 2),
                    "vol_spike": round(vol_15m / vol_median_4h, 2),
                    "depth_usd": round(depth_usd, 0),
                    "funding_rate": round(funding_rate, 6),
                    "size_multiplier": size_mult,
                },
            }
            signals.append(signal)
            logger.info("meme_signal_found", ticker=sym, change_1h=round(change_1h_pct, 2),
                        vol_spike=round(vol_15m / vol_median_4h, 2))

        except Exception as e:
            logger.debug("meme_scan_error", ticker=sym, error=str(e))
            continue

    logger.info("meme_scan_done", candidates=len(perps), signals=len(signals))
    return signals


def _estimate_atr(klines: list[dict]) -> float:
    """Simple ATR from recent klines."""
    if len(klines) < 2:
        return 0
    ranges = []
    for k in klines[-5:]:
        h = float(k.get("high", 0))
        l = float(k.get("low", 0))
        if h > 0 and l > 0:
            ranges.append(h - l)
    return statistics.mean(ranges) if ranges else 0


def _calc_confidence(change_1h: float, change_15m: float, vol_now: float, vol_median: float) -> int:
    """Simple confidence score based on momentum strength."""
    score = 30  # base
    if change_1h > 15:
        score += 25
    elif change_1h > 10:
        score += 15
    elif change_1h > 8:
        score += 10
    if change_15m > 5:
        score += 20
    elif change_15m > 3:
        score += 10
    vol_ratio = vol_now / vol_median if vol_median > 0 else 0
    if vol_ratio > 5:
        score += 15
    elif vol_ratio > 3:
        score += 10
    return min(score, 100)
