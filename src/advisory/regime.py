"""Karsa Trading System - Macro Regime Filter"""

import time
from typing import Any

# In-memory cache for regime data (avoid hammering TradingView)
_regime_cache: dict = {}
_regime_cache_ttl = 300  # 5 minutes


class MacroRegimeFilter:
    """Filter trading signals based on macro market conditions.

    Regime states:
    - BULL: VIX < 20, SPY > 200 SMA -> aggressive long bias
    - NEUTRAL: VIX 20-25, SPY near 200 SMA -> reduced position sizes
    - BEAR: VIX > 25, SPY < 200 SMA -> defensive, cash-heavy
    """

    def __init__(self, mcp_client: Any):
        self.mcp = mcp_client

    async def get_current_regime(self) -> dict:
        """Determine current market regime.

        Returns:
            dict with regime info: {state: str, vix: float, spy_price: float, spy_sma200: float, recommendation: str}
        """
        # Check cache
        global _regime_cache
        if _regime_cache and time.time() - _regime_cache.get("ts", 0) < _regime_cache_ttl:
            return _regime_cache["data"]

        try:
            # Get SPY quote (most important)
            spy_quote = await self.mcp.get_quote("SPY", "US")
            spy_price = spy_quote.get("price", 0.0) if not spy_quote.get("error") else 0.0

            # Get SPY 200 SMA
            spy_sma200_data = await self.mcp.get_ema("SPY", "US", 200)
            spy_sma200 = spy_sma200_data.get("value", 0.0) if not spy_sma200_data.get("error") else spy_price

            # Try VIX (might not work with TradingView, use fallback)
            vix_price = 18.0  # Default assumption
            try:
                vix_quote = await self.mcp.get_quote("VIX", "US")
                if not vix_quote.get("error") and vix_quote.get("price"):
                    vix_price = vix_quote["price"]
            except Exception:
                pass  # Use default

            # If SPY data unavailable, return cached or default
            if spy_price == 0.0:
                return {
                    "state": "UNKNOWN",
                    "vix": vix_price,
                    "spy_price": "N/A",
                    "spy_sma200": "N/A",
                    "recommendation": "Data unavailable. Using cached regime if available.",
                    "error": "SPY data unavailable"
                }

            # Determine regime
            if vix_price < 20 and spy_price > spy_sma200:
                regime = "BULL"
                recommendation = "Aggressive long bias. Full position sizing."
            elif vix_price > 25 or spy_price < spy_sma200:
                regime = "BEAR"
                recommendation = "Defensive mode. Cut position sizes by 50%. Increase cash."
            else:
                regime = "NEUTRAL"
                recommendation = "Standard conditions. Normal position sizing."

            result = {
                "state": regime,
                "vix": vix_price,
                "spy_price": spy_price,
                "spy_sma200": spy_sma200,
                "recommendation": recommendation
            }

            # Cache result
            _regime_cache = {"data": result, "ts": time.time()}
            return result

        except Exception as e:
            # Return cached data if available, otherwise UNKNOWN
            if _regime_cache and "data" in _regime_cache:
                return _regime_cache["data"]
            return {"state": "UNKNOWN", "error": str(e)}

    def get_position_size_multiplier(self, regime: str) -> float:
        """Get position size multiplier based on regime.

        Args:
            regime: "BULL", "NEUTRAL", "BEAR", or "UNKNOWN"

        Returns:
            float multiplier (e.g., 1.0 for normal, 0.5 for bear)
        """
        if regime == "BULL":
            return 1.2
        elif regime == "BEAR":
            return 0.5
        else:
            return 1.0
