"""Karsa Trading System - Indonesia Market Intelligence Engine

Composite regime scoring (IHSG breadth + sector rotation + foreign flow + price structure),
flow tracking (volume-based proxy), earnings calendar with blackout windows.
"""

import json
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any

from src.utils.logging import get_logger

logger = get_logger("idx_intelligence")

# --- Sector universe (extends IDX_UNIVERSE for breadth) ---
IDX_SECTORS: dict[str, list[str]] = {
    "BANKING": ["BBCA", "BBRI", "BMRI", "BBNI", "BRIS"],
    "TELCO": ["TLKM", "EXCL", "ISAT", "FREN"],
    "CONSUMER": ["UNVR", "ICBP", "KLBF", "HMSP"],
    "AUTO": ["ASII", "GJTL", "SMSM"],
    "ENERGY": ["PGAS", "ADRO", "ITMG"],
    "TECH": ["GOTO", "BUKA", "EMTEK"],
    "INFRA": ["JSMR", "WTON"],
    "MINING": ["ADRO", "ITMG", "PTBA"],
}

# All unique tickers for breadth calculation (~25)
BREADTH_UNIVERSE: list[str] = sorted(set(
    t for tickers in IDX_SECTORS.values() for t in tickers
))

# Sector rotation signal thresholds
_SECTOR_STRONG_PCT = 1.5   # avg change > 1.5% = strong
_SECTOR_WEAK_PCT = -1.5    # avg change < -1.5% = weak

# Composite weights
_WEIGHT_BREADTH = 0.30
_WEIGHT_SECTOR = 0.25
_WEIGHT_FLOW = 0.20
_WEIGHT_PRICE = 0.25


class FlowTracker:
    """Foreign flow proxy — derived from volume and price action.

    No direct broker feed available. Uses volume surge + price direction
    as proxy for foreign activity (foreign tend to move volume).
    """

    def __init__(self, mcp: Any, cache: Any = None):
        self.mcp = mcp
        self.cache = cache
        self._flow_cache: dict[str, dict] = {}
        self._cache_ttl = 300  # 5 min

    async def get_ticker_flow(self, ticker: str) -> dict:
        """Get 3-day flow proxy for a ticker.

        Returns:
            {ticker, net_flow_3d_pct, daily_flows: [{date, price_change_pct, volume_ratio, flow_estimate}], signal}
        """
        cache_key = f"flow_{ticker}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            # Get 3 days of daily OHLCV
            ohlcv = await self.mcp.get_ohlcv(ticker, "IDX", timeframe="1D", limit=5)
            if not ohlcv or len(ohlcv) < 2:
                return self._empty_flow(ticker)

            # Get current quote for avg volume comparison
            quote = await self.mcp.get_quote(ticker, "IDX")
            avg_volume = quote.get("volume", 0) if not quote.get("error") else 0

            daily_flows = []
            for i in range(len(ohlcv) - 1, 0, -1):
                candle = ohlcv[i]
                prev_candle = ohlcv[i - 1]
                price_change_pct = (
                    (candle["close"] - prev_candle["close"]) / prev_candle["close"] * 100
                    if prev_candle["close"] > 0 else 0
                )
                volume_ratio = (
                    candle["volume"] / prev_candle["volume"]
                    if prev_candle["volume"] > 0 else 1.0
                )
                flow_estimate = self._derive_flow(price_change_pct, volume_ratio)
                daily_flows.append({
                    "date": candle.get("timestamp", ""),
                    "price_change_pct": round(price_change_pct, 2),
                    "volume_ratio": round(volume_ratio, 2),
                    "flow_estimate": round(flow_estimate, 2),
                })

            # 3-day net flow
            recent = daily_flows[-3:] if len(daily_flows) >= 3 else daily_flows
            net_flow = sum(d["flow_estimate"] for d in recent)

            signal = "NEUTRAL"
            if net_flow > 7:
                signal = "STRONG_BUY"
            elif net_flow > 3:
                signal = "BUY"
            elif net_flow < -7:
                signal = "STRONG_SELL"
            elif net_flow < -3:
                signal = "SELL"

            result = {
                "ticker": ticker,
                "net_flow_3d_pct": round(net_flow, 2),
                "daily_flows": daily_flows,
                "signal": signal,
            }
            self._set_cached(cache_key, result)
            return result

        except Exception as e:
            logger.error("flow_tracker_failed", ticker=ticker, error=str(e))
            return self._empty_flow(ticker)

    def _derive_flow(self, price_change_pct: float, volume_ratio: float) -> float:
        """Derive flow estimate from price change and volume ratio.

        Positive = foreign buying proxy, negative = foreign selling proxy.
        Volume surge (>1.5x) amplifies the signal.
        """
        if abs(price_change_pct) < 0.1:
            return 0.0

        direction = 1.0 if price_change_pct > 0 else -1.0
        magnitude = min(abs(price_change_pct), 10.0)  # cap at 10%
        volume_amp = min(volume_ratio, 5.0)  # cap amplification at 5x

        return direction * magnitude * (1 + (volume_amp - 1) * 0.3)

    def _empty_flow(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "net_flow_3d_pct": 0.0,
            "daily_flows": [],
            "signal": "NEUTRAL",
        }

    def _get_cached(self, key: str) -> dict | None:
        entry = self._flow_cache.get(key)
        if entry and time.time() - entry.get("ts", 0) < self._cache_ttl:
            return entry["data"]
        return None

    def _set_cached(self, key: str, data: dict):
        self._flow_cache[key] = {"data": data, "ts": time.time()}


class EarningsCalendar:
    """IDX earnings calendar with blackout window logic.

    Loads from static JSON file. Supports DB overlay for manual updates.
    """

    def __init__(self, calendar_path: str | None = None):
        if calendar_path is None:
            calendar_path = str(Path(__file__).parent / "earnings_calendar.json")
        self._calendar_path = calendar_path
        self._earnings: list[dict] = []
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        try:
            with open(self._calendar_path, "r") as f:
                data = json.load(f)
            self._earnings = data.get("earnings", [])
            self._loaded = True
        except FileNotFoundError:
            logger.warning("earnings_calendar_not_found", path=self._calendar_path)
            self._earnings = []
            self._loaded = True
        except Exception as e:
            logger.error("earnings_calendar_load_failed", error=str(e))
            self._earnings = []
            self._loaded = True

    def get_earnings(self, ticker: str) -> dict | None:
        """Get next earnings report for a ticker."""
        self._load()
        today = date.today()
        for entry in self._earnings:
            if entry.get("ticker") != ticker.upper():
                continue
            report_date = self._parse_date(entry.get("report_date"))
            if report_date and report_date >= today:
                return {
                    "ticker": ticker.upper(),
                    "next_report": entry["report_date"],
                    "fiscal_quarter": entry.get("fiscal_quarter"),
                    "expected_eps": entry.get("expected_eps"),
                    "guidance": entry.get("guidance"),
                    "days_until": (report_date - today).days,
                }
        return None

    def is_blackout(self, ticker: str, buffer_days: int = 5) -> bool:
        """Check if ticker is within earnings blackout window."""
        earnings = self.get_earnings(ticker)
        if not earnings:
            return False
        days = earnings["days_until"]
        return 0 <= days <= buffer_days

    def get_blackout_universe(self, buffer_days: int = 5) -> list[str]:
        """Get all tickers currently in earnings blackout."""
        self._load()
        today = date.today()
        cutoff = today + timedelta(days=buffer_days)
        blackout = []
        for entry in self._earnings:
            report_date = self._parse_date(entry.get("report_date"))
            if report_date and today <= report_date <= cutoff:
                blackout.append(entry["ticker"])
        return blackout

    def get_upcoming(self, days: int = 30) -> list[dict]:
        """Get all earnings within the next N days."""
        self._load()
        today = date.today()
        cutoff = today + timedelta(days=days)
        upcoming = []
        for entry in self._earnings:
            report_date = self._parse_date(entry.get("report_date"))
            if report_date and today <= report_date <= cutoff:
                upcoming.append({
                    "ticker": entry["ticker"],
                    "report_date": entry["report_date"],
                    "fiscal_quarter": entry.get("fiscal_quarter"),
                    "days_until": (report_date - today).days,
                })
        return sorted(upcoming, key=lambda x: x["days_until"])

    @staticmethod
    def _parse_date(date_str: str | None) -> date | None:
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None


class IDXMarketIntelligence:
    """Indonesia Market Intelligence — breadth, sector rotation, flow, composite scoring."""

    def __init__(self, mcp: Any, cache: Any = None):
        self.mcp = mcp
        self.cache = cache
        self.flow_tracker = FlowTracker(mcp, cache)
        self.earnings = EarningsCalendar()
        self._breadth_cache: dict[str, Any] = {}
        self._breadth_cache_ts = 0
        self._sector_cache: list[dict] = []
        self._sector_cache_ts = 0
        self._cache_ttl = 300  # 5 min

    async def get_breadth_metrics(self) -> dict:
        """Compute IHSG market breadth from BREADTH_UNIVERSE.

        Returns:
            {advancing, declining, unchanged, total, breadth_ratio, advancing_pct}
        """
        if time.time() - self._breadth_cache_ts < self._cache_ttl and self._breadth_cache:
            return self._breadth_cache

        advancing = declining = unchanged = 0

        for ticker in BREADTH_UNIVERSE:
            try:
                quote = await self.mcp.get_quote(ticker, "IDX")
                if quote.get("error"):
                    continue
                change_pct = quote.get("change_pct", 0)
                if change_pct > 0.1:
                    advancing += 1
                elif change_pct < -0.1:
                    declining += 1
                else:
                    unchanged += 1
            except Exception:
                continue

        total = advancing + declining + unchanged
        breadth_ratio = advancing / declining if declining > 0 else float(advancing)

        result = {
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "total": total,
            "breadth_ratio": round(breadth_ratio, 2),
            "advancing_pct": round(advancing / total * 100, 1) if total > 0 else 0,
        }
        self._breadth_cache = result
        self._breadth_cache_ts = time.time()
        return result

    async def get_sector_performance(self) -> list[dict]:
        """Compute sector rotation from IDX_SECTORS.

        Returns:
            [{sector, avg_change_pct, advancers, decliners, foreign_flow, rotation_signal}]
        """
        if time.time() - self._sector_cache_ts < self._cache_ttl and self._sector_cache:
            return self._sector_cache

        sectors = []
        for sector, tickers in IDX_SECTORS.items():
            changes = []
            advancers = decliners = 0
            total_flow = 0.0

            for ticker in tickers:
                try:
                    quote = await self.mcp.get_quote(ticker, "IDX")
                    if quote.get("error"):
                        continue
                    change_pct = quote.get("change_pct", 0)
                    changes.append(change_pct)
                    if change_pct > 0.1:
                        advancers += 1
                    elif change_pct < -0.1:
                        decliners += 1

                    # Flow proxy
                    flow = await self.flow_tracker.get_ticker_flow(ticker)
                    total_flow += flow.get("net_flow_3d_pct", 0)
                except Exception:
                    continue

            avg_change = sum(changes) / len(changes) if changes else 0
            avg_flow = total_flow / len(tickers) if tickers else 0

            rotation = "NEUTRAL"
            if avg_change > _SECTOR_STRONG_PCT and avg_flow > 0:
                rotation = "LEADING"
            elif avg_change < _SECTOR_WEAK_PCT and avg_flow < 0:
                rotation = "LAGGING"
            elif avg_change > 0 and avg_flow > 0:
                rotation = "IMPROVING"
            elif avg_change < 0 and avg_flow < 0:
                rotation = "WEAKENING"

            sectors.append({
                "sector": sector,
                "avg_change_pct": round(avg_change, 2),
                "advancers": advancers,
                "decliners": decliners,
                "foreign_flow": round(avg_flow, 2),
                "rotation_signal": rotation,
            })

        sectors.sort(key=lambda s: s["avg_change_pct"], reverse=True)
        self._sector_cache = sectors
        self._sector_cache_ts = time.time()
        return sectors

    async def get_regime_composite(self) -> dict:
        """Compute composite regime score from 4 dimensions.

        Returns:
            {score: -100..+100, state: STRONG_BULL|BULL|NEUTRAL|BEAR|STRONG_BEAR,
             components: {breadth, sector, flow, price}, triggers: [...]}
        """
        breadth = await self.get_breadth_metrics()
        sectors = await self.get_sector_performance()

        # Score each dimension (-100 to +100)
        breadth_score = self._score_breadth(breadth)
        sector_score = self._score_sector(sectors)
        flow_score = self._score_flow_from_sectors(sectors)
        price_score = await self._score_price()

        # Weighted composite
        composite = (
            breadth_score * _WEIGHT_BREADTH
            + sector_score * _WEIGHT_SECTOR
            + flow_score * _WEIGHT_FLOW
            + price_score * _WEIGHT_PRICE
        )
        composite = max(-100, min(100, round(composite, 1)))

        # State classification
        if composite >= 50:
            state = "STRONG_BULL"
        elif composite >= 15:
            state = "BULL"
        elif composite >= -15:
            state = "NEUTRAL"
        elif composite >= -50:
            state = "BEAR"
        else:
            state = "STRONG_BEAR"

        # Triggers — things that moved the needle
        triggers = []
        if breadth["advancing_pct"] >= 70:
            triggers.append(f"Breadth surge: {breadth['advancing_pct']}% advancing")
        elif breadth["advancing_pct"] <= 30:
            triggers.append(f"Breadth collapse: only {breadth['advancing_pct']}% advancing")

        strong_sectors = [s for s in sectors if s["rotation_signal"] == "LEADING"]
        weak_sectors = [s for s in sectors if s["rotation_signal"] == "LAGGING"]
        if strong_sectors:
            triggers.append(f"Leading sectors: {', '.join(s['sector'] for s in strong_sectors)}")
        if weak_sectors:
            triggers.append(f"LAGGING sectors: {', '.join(s['sector'] for s in weak_sectors)}")

        if price_score <= -60:
            triggers.append("IHSG below key support levels")
        elif price_score >= 60:
            triggers.append("IHSG above key resistance levels")

        # Earnings blackout check
        blackout = self.earnings.get_blackout_universe()
        if blackout:
            triggers.append(f"Earnings blackout: {', '.join(blackout)}")

        return {
            "score": composite,
            "state": state,
            "components": {
                "breadth": {"score": round(breadth_score, 1), "weight": _WEIGHT_BREADTH, **breadth},
                "sector": {"score": round(sector_score, 1), "weight": _WEIGHT_SECTOR, "sectors": sectors},
                "flow": {"score": round(flow_score, 1), "weight": _WEIGHT_FLOW},
                "price": {"score": round(price_score, 1), "weight": _WEIGHT_PRICE},
            },
            "triggers": triggers,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _score_breadth(self, breadth: dict) -> float:
        """Score breadth from -100 to +100."""
        adv_pct = breadth.get("advancing_pct", 50)
        ratio = breadth.get("breadth_ratio", 1.0)

        # adv_pct: 50% = neutral (0), 80% = +100, 20% = -100
        pct_score = max(-100, min(100, (adv_pct - 50) * 3.33))

        # ratio: 1.0 = neutral (0), 3.0 = +100, 0.33 = -100
        if ratio >= 1:
            ratio_score = min(100, (ratio - 1) * 50)
        else:
            ratio_score = max(-100, (ratio - 1) * 100)

        return (pct_score + ratio_score) / 2

    def _score_sector(self, sectors: list[dict]) -> float:
        """Score sector rotation from -100 to +100."""
        if not sectors:
            return 0.0

        rotation_scores = {
            "LEADING": 80,
            "IMPROVING": 30,
            "NEUTRAL": 0,
            "WEAKENING": -30,
            "LAGGING": -80,
        }
        total = sum(rotation_scores.get(s["rotation_signal"], 0) for s in sectors)
        return max(-100, min(100, total / len(sectors)))

    def _score_flow_from_sectors(self, sectors: list[dict]) -> float:
        """Score foreign flow from sector data (-100 to +100)."""
        if not sectors:
            return 0.0

        avg_flow = sum(s.get("foreign_flow", 0) for s in sectors) / len(sectors)
        # ±10% flow = ±100 score
        return max(-100, min(100, avg_flow * 10))

    async def _score_price(self) -> float:
        """Score IHSG price structure (-100 to +100).

        Uses IHSG vs SMA20, SMA50, SMA200.
        """
        try:
            # Get IHSG quote
            ihsg_quote = await self.mcp.get_quote("IHSG", "IDX")
            if ihsg_quote.get("error"):
                # Fallback to BBCA as proxy
                ihsg_quote = await self.mcp.get_quote("BBCA", "IDX")

            price = ihsg_quote.get("price", 0)
            if price <= 0:
                return 0.0

            sma20 = await self.mcp.get_ema("IHSG", "IDX", 20)
            sma200 = await self.mcp.get_ema("IHSG", "IDX", 200)

            score = 0.0
            if sma20 and sma20 > 0:
                if price > sma20:
                    score += 30
                else:
                    score -= 30

            if sma200 and sma200 > 0:
                pct_above_sma200 = (price - sma200) / sma200 * 100
                # ±5% from SMA200 = ±50 points
                score += max(-50, min(50, pct_above_sma200 * 10))

            # RSI component (IHSG may not resolve on TradingView — fallback to BBCA)
            rsi = await self.mcp.get_rsi("IHSG", "IDX")
            if rsi == 50.0:  # default fallback value = not resolved
                rsi = await self.mcp.get_rsi("BBCA", "IDX")
            if rsi > 70:
                score -= 20  # overbought
            elif rsi < 30:
                score += 20  # oversold

            return max(-100, min(100, score))

        except Exception as e:
            logger.error("price_score_failed", error=str(e))
            return 0.0

    async def get_ihsg_support_resistance(self) -> dict:
        """Get IHSG support/resistance levels."""
        try:
            ihsg_quote = await self.mcp.get_quote("IHSG", "IDX")
            if ihsg_quote.get("error"):
                ihsg_quote = await self.mcp.get_quote("BBCA", "IDX")

            price = ihsg_quote.get("price", 0)
            high = ihsg_quote.get("high", 0)
            low = ihsg_quote.get("low", 0)

            sma20 = await self.mcp.get_ema("IHSG", "IDX", 20)
            sma50 = await self.mcp.get_ema("IHSG", "IDX", 50)
            sma200 = await self.mcp.get_ema("IHSG", "IDX", 200)

            bb = await self.mcp.get_bollinger("IHSG", "IDX")

            pivot = (high + low + price) / 3 if (high and low and price) else price
            r1 = 2 * pivot - low if low else price * 1.01
            s1 = 2 * pivot - high if high else price * 0.99

            return {
                "current": price,
                "sma20": round(sma20, 2) if sma20 else None,
                "sma50": round(sma50, 2) if sma50 else None,
                "sma200": round(sma200, 2) if sma200 else None,
                "pivot": round(pivot, 2),
                "r1": round(r1, 2),
                "s1": round(s1, 2),
                "bb_upper": bb.get("upper"),
                "bb_middle": bb.get("middle"),
                "bb_lower": bb.get("lower"),
            }
        except Exception as e:
            logger.error("support_resistance_failed", error=str(e))
            return {"current": 0, "error": str(e)}
