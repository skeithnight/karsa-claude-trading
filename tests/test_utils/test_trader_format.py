"""Tests for trader formatting engine."""
import pytest
from src.utils.trader_format import (
    funding_gauge, regime_banner, signal_card,
    perf_dashboard, market_snapshot_card, briefing_block
)

def test_funding_gauge():
    # Test positive funding
    gauge = funding_gauge(0.0003)
    assert "🔴" in gauge
    assert "+0.0300%" in gauge
    
    # Test negative funding
    gauge = funding_gauge(-0.0001)
    assert "🟢" in gauge
    assert "-0.0100%" in gauge
    
    # Test neutral funding
    gauge = funding_gauge(0)
    assert "⚪️" in gauge

def test_regime_banner():
    banner = regime_banner("TREND_BULL", 0.55, 30.0, "Long bias.")
    assert "REGIME: TREND_BULL" in banner
    assert "Hurst Exponent: 0.55" in banner
    assert "Long bias." in banner

def test_signal_card():
    card = signal_card(
        ticker="BTCUSDT",
        direction="LONG",
        confidence=80,
        entry=100000.0,
        sl=98000.0,
        tp=106000.0,
        reasoning="Bullish breakout"
    )
    assert "LONG INITIATED — BTCUSDT" in card
    assert "Entry   : $100,000.00" in card
    assert "Stop    : $98,000.00" in card
    assert "Target  : $106,000.00" in card
    assert "R/R: 3.00:1" in card
    assert "Bullish breakout" in card

def test_perf_dashboard():
    dash = perf_dashboard(
        win_rate=60.0,
        avg_win=2.5,
        avg_loss=-1.0,
        total_pnl=500.0,
        total_trades=10
    )
    assert "PERFORMANCE DASHBOARD" in dash
    assert "Win Rate  :" in dash
    assert "Realized  : 🟢 $+500.00" in dash
    assert "Avg Win +2.50%" in dash

def test_market_snapshot_card():
    quote = {"last_price": 50000.0, "change_24h_pct": 2.5}
    ta = {
        "rsi": {"rsi": 65.0, "signal": "bullish"},
        "bollinger": {"signal": "within_bands"},
        "macd": {"crossover": "neutral"},
        "ema_20": {"ema": 49000.0},
        "ema_50": {"ema": 48000.0}
    }
    card = market_snapshot_card("BTCUSDT", quote, ta, 0.0001, 5000000.0)
    assert "SNAPSHOT: BTCUSDT" in card
    assert "Price    : $50,000.00" in card
    assert "RSI(14) : 65.0" in card
    assert "Open Int : $5,000,000" in card

def test_briefing_block():
    regime = {
        "state": "TREND_BULL",
        "hurst": 0.55,
        "adx": 30.0,
        "recommendation": "Long bias.",
        "benchmark_price": 95000.0,
        "btc_dominance": 56.5,
        "market_season": "BTC_SEASON"
    }
    top_movers = [
        {"symbol": "BTCUSDT", "last_price": 95000.0, "change_24h_pct": 3.2},
        {"symbol": "ETHUSDT", "last_price": 3200.0, "change_24h_pct": -1.5}
    ]
    funding_alerts = [
        {"symbol": "SOLUSDT", "funding_rate": 0.0006}
    ]
    brief = briefing_block(regime, top_movers, funding_alerts)
    assert "COIN DESK BRIEFING" in brief
    assert "State     : 🟢 TREND_BULL" in brief
    assert "BTC Dom   : 56.5%" in brief
    assert "BTCUSDT" in brief
    assert "ETHUSDT" in brief
    assert "SOLUSDT" in brief
