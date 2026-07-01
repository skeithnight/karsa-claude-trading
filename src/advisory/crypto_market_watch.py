"""src/advisory/crypto_market_watch.py — Crypto Universe Aggregator and Market Intelligence Engine"""
import asyncio
from src.advisory.crypto_universe import CRYPTO_UNIVERSE
from src.advisory.crypto_technicals import full_analysis

class CryptoMarketWatchEngine:
    """Aggregates real-time market data, technical analyses, and funding alerts across the universe."""

    @staticmethod
    async def get_top_movers(mcp_client, n: int = 5) -> list[dict]:
        """Rank crypto universe symbols by absolute 24h change %."""
        tasks = [mcp_client.get_quote(symbol, "CRYPTO") for symbol in CRYPTO_UNIVERSE]
        quotes = await asyncio.gather(*tasks, return_exceptions=True)
        
        valid_quotes = []
        for symbol, quote in zip(CRYPTO_UNIVERSE, quotes):
            if isinstance(quote, Exception) or quote.get("error"):
                continue
            
            # Map standard Bybit response fields
            last_price = quote.get("price", 0.0)
            change_24h_pct = quote.get("change_pct", 0.0)
            
            valid_quotes.append({
                "symbol": symbol,
                "last_price": last_price,
                "change_24h_pct": change_24h_pct
            })
            
        # Sort by absolute 24h change descending
        valid_quotes.sort(key=lambda q: abs(q["change_24h_pct"]), reverse=True)
        return valid_quotes[:n]

    @staticmethod
    async def get_funding_alerts(mcp_client, threshold: float = 0.0003) -> list[dict]:
        """Identify symbols with funding rates exceeding absolute threshold (crowding indicators)."""
        tasks = [mcp_client.get_quote(symbol, "CRYPTO") for symbol in CRYPTO_UNIVERSE]
        quotes = await asyncio.gather(*tasks, return_exceptions=True)
        
        alerts = []
        for symbol, quote in zip(CRYPTO_UNIVERSE, quotes):
            if isinstance(quote, Exception) or quote.get("error"):
                continue
                
            funding_rate = quote.get("funding_rate", 0.0)
            if abs(funding_rate) >= threshold:
                alerts.append({
                    "symbol": symbol,
                    "funding_rate": funding_rate
                })
        return alerts

    @staticmethod
    async def get_market_snapshot(mcp_client, ticker: str) -> dict:
        """Gathers price, full technical indicators, funding rate, and open interest for a coin."""
        quote = await mcp_client.get_quote(ticker, "CRYPTO")
        if quote.get("error"):
            return {"error": quote["error"]}
            
        ohlcv = await mcp_client.get_ohlcv(ticker, "CRYPTO", timeframe="4h", limit=200)
        if not ohlcv:
            return {"error": f"No OHLCV candles found for {ticker}"}
            
        ta = full_analysis(ohlcv)
        
        # Norm data formats
        snapshot_quote = {
            "last_price": quote.get("price", 0.0),
            "change_24h_pct": quote.get("change_pct", 0.0),
        }
        
        return {
            "ticker": ticker,
            "quote": snapshot_quote,
            "ta": ta,
            "funding": quote.get("funding_rate", 0.0),
            "open_interest": quote.get("open_interest", 0.0)
        }

    @classmethod
    async def get_briefing_data(cls, mcp_client, regime: dict) -> dict:
        """Fetch all components of the market watch briefing in parallel."""
        movers_task = cls.get_top_movers(mcp_client)
        funding_task = cls.get_funding_alerts(mcp_client)
        
        movers, funding = await asyncio.gather(movers_task, funding_task)
        
        return {
            "regime": regime,
            "top_movers": movers,
            "funding_alerts": funding
        }
