"""Karsa Trading System — Deterministic Crypto Technical Indicators

Pure Python implementations. No LLM involvement.
Used by CryptoAnalyst agent tools instead of raw OHLCV reasoning.
"""

import math
from src.utils.logging import get_logger

logger = get_logger("crypto_technicals")


def _validate_ohlcv(ohlcv: list[dict], min_len: int = 2) -> bool:
    if not ohlcv or len(ohlcv) < min_len:
        return False
    required = {"open", "high", "low", "close", "volume"}
    return all(required.issubset(c.keys()) for c in ohlcv[:min_len])


def calculate_rsi(ohlcv: list[dict], period: int = 14) -> dict:
    """Calculate RSI. Returns: {rsi, period, overbought, oversold, signal}"""
    if not _validate_ohlcv(ohlcv, period + 1):
        return {"rsi": 50.0, "period": period, "error": "insufficient_data"}

    closes = [c["close"] for c in ohlcv]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        rsi = 50.0 if avg_gain == 0 else 100.0
    else:
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

    overbought = rsi > 70
    oversold = rsi < 30
    signal = ("overbought" if overbought else "oversold" if oversold
              else "bullish" if rsi > 60 else "bearish" if rsi < 40 else "neutral")

    return {"rsi": round(rsi, 2), "period": period, "overbought": overbought,
            "oversold": oversold, "signal": signal}


def calculate_bollinger(ohlcv: list[dict], period: int = 20, std_dev: float = 2.0, bbw_lookback: int = 0) -> dict:
    """Calculate Bollinger Bands. Returns: {upper, middle, lower, bandwidth, pct_b, signal, bbw_percentile}"""
    if not _validate_ohlcv(ohlcv, period):
        return {"error": "insufficient_data"}

    needed_len = max(1, bbw_lookback) + period - 1
    recent_ohlcv = ohlcv[-needed_len:] if len(ohlcv) >= needed_len else ohlcv
    closes = [c["close"] for c in recent_ohlcv]

    if len(closes) < period:
        return {"error": "insufficient_data"}

    bandwidths = []
    current_upper = current_middle = current_lower = 0.0

    for i in range(period, len(closes) + 1):
        window = closes[i - period:i]
        mean = sum(window) / period
        std = math.sqrt(sum((c - mean) ** 2 for c in window) / period)
        upper = mean + (std_dev * std)
        lower = mean - (std_dev * std)
        bw = (upper - lower) / mean * 100 if mean > 0 else 0
        bandwidths.append(bw)
        
        if i == len(closes):
            current_upper = upper
            current_middle = mean
            current_lower = lower

    current_price = closes[-1]
    current_bandwidth = bandwidths[-1] if bandwidths else 0
    pct_b = (current_price - current_lower) / (current_upper - current_lower) if (current_upper - current_lower) > 0 else 0.5

    signal = ("above_upper" if pct_b > 1.0 else "below_lower" if pct_b < 0.0
              else "near_upper" if pct_b > 0.8 else "near_lower" if pct_b < 0.2
              else "within_bands")

    result = {
        "upper": round(current_upper, 4), "middle": round(current_middle, 4), "lower": round(current_lower, 4),
        "bandwidth": round(current_bandwidth, 2), "pct_b": round(pct_b, 4), "signal": signal, "period": period
    }

    if bbw_lookback > 0 and len(bandwidths) >= 2:
        less_than = sum(1 for bw in bandwidths if bw < current_bandwidth)
        percentile = (less_than / len(bandwidths)) * 100
        result["bbw_percentile"] = round(percentile, 2)
    else:
        result["bbw_percentile"] = None

    return result


def calculate_ema(ohlcv: list[dict], period: int = 20) -> dict:
    """Calculate EMA. Returns: {ema, period, price_vs_ema, distance_pct}"""
    if not _validate_ohlcv(ohlcv, period):
        return {"error": "insufficient_data"}

    closes = [c["close"] for c in ohlcv]
    multiplier = 2 / (period + 1)

    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    current_price = closes[-1]
    distance_pct = (current_price - ema) / ema * 100 if ema > 0 else 0

    return {"ema": round(ema, 4), "period": period,
            "price_vs_ema": "above" if current_price > ema else "below",
            "distance_pct": round(distance_pct, 2)}


def calculate_macd(ohlcv: list[dict], fast: int = 12, slow: int = 26, signal_period: int = 9) -> dict:
    """Calculate MACD. Returns: {macd, signal_line, histogram, crossover}"""
    min_len = slow + signal_period
    if not _validate_ohlcv(ohlcv, min_len):
        return {"error": "insufficient_data"}

    closes = [c["close"] for c in ohlcv]

    def _ema(data, period):
        m = 2 / (period + 1)
        result = [sum(data[:period]) / period]
        for val in data[period:]:
            result.append((val - result[-1]) * m + result[-1])
        return result

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    signal_line = _ema(macd_line, signal_period)

    histogram = macd_line[-1] - signal_line[-1]
    prev_histogram = macd_line[-2] - signal_line[-2] if len(signal_line) >= 2 else histogram

    crossover = ("bullish_cross" if prev_histogram <= 0 < histogram
                 else "bearish_cross" if prev_histogram >= 0 > histogram
                 else "bullish" if histogram > 0 else "bearish" if histogram < 0 else "neutral")

    return {"macd": round(macd_line[-1], 4), "signal_line": round(signal_line[-1], 4),
            "histogram": round(histogram, 4), "crossover": crossover}


def calculate_atr(ohlcv: list[dict], period: int = 14) -> dict:
    """Calculate ATR. Returns: {atr, atr_pct, period, volatility}"""
    if not _validate_ohlcv(ohlcv, period + 1):
        return {"error": "insufficient_data"}

    true_ranges = []
    for i in range(1, len(ohlcv)):
        h, l, prev_c = ohlcv[i]["high"], ohlcv[i]["low"], ohlcv[i - 1]["close"]
        true_ranges.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))

    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    current_price = ohlcv[-1]["close"]
    atr_pct = atr / current_price * 100 if current_price > 0 else 0

    volatility = ("extreme" if atr_pct > 5 else "high" if atr_pct > 3
                   else "moderate" if atr_pct > 1.5 else "low")

    return {"atr": round(atr, 4), "atr_pct": round(atr_pct, 2),
            "period": period, "volatility": volatility}


def full_analysis(ohlcv: list[dict], ob_imbalance: float = 0.0) -> dict:
    """Run all indicators and include orderbook imbalance data."""
    # ponytail: ob_imbalance from WebSockets directly injected here
    return {
        "rsi": calculate_rsi(ohlcv),
        "bollinger": calculate_bollinger(ohlcv),
        "ema_20": calculate_ema(ohlcv, 20),
        "ema_50": calculate_ema(ohlcv, 50),
        "macd": calculate_macd(ohlcv),
        "atr": calculate_atr(ohlcv),
        "orderbook_imbalance": {
            "value": round(ob_imbalance, 4),
            "signal": "strong_buy" if ob_imbalance > 0.4 else "buy" if ob_imbalance > 0.1 else \
                      "strong_sell" if ob_imbalance < -0.4 else "sell" if ob_imbalance < -0.1 else "neutral",
            "meaning": "Positive = Bid side heavy (bullish), Negative = Ask side heavy (bearish)"
        }
    }


# --- Self-Test ---
if __name__ == "__main__":
    _test = [{"open": 100 + i * 0.5, "high": 101 + i * 0.5, "low": 99 + i * 0.5,
              "close": 100.5 + i * 0.5, "volume": 1000} for i in range(60)]

    rsi = calculate_rsi(_test)
    assert 50 < rsi["rsi"] <= 100, f"RSI bullish in uptrend: {rsi}"

    bb = calculate_bollinger(_test)
    assert bb["upper"] > bb["lower"]

    atr = calculate_atr(_test)
    assert atr["atr"] > 0

    print(f"All self-tests passed. RSI={rsi['rsi']} BB={bb['signal']} ATR={atr['volatility']}")
