import asyncio
import os
from src.data.mcp_client import MCPClient
from src.advisory.coin_regime import CoinRegimeEngine

async def main():
    import redis.asyncio as redis
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = redis.from_url(redis_url, decode_responses=True)
    
    # Mock cache structure expected by CoinRegimeEngine (it expects BybitClient's cache which wraps redis)
    class DummyCache:
        def __init__(self, r):
            self.client = r
            
    cache = DummyCache(r)
    from src.data.cache import CacheManager
    cache_mgr = CacheManager(r)
    mcp = MCPClient(cache_mgr)
    engine = CoinRegimeEngine(mcp, cache_mgr)
    
    symbol = "BTCUSDT"
    print(f"Fetching regime for {symbol}...")
    regime = await engine.get_regime(symbol)
    print("Regime State:", regime.state)
    print("15m ADX:", regime.adx_15m)
    print("4H ADX:", regime.adx_4h)
    print("1D ADX:", regime.adx_1d)
    print("BBW Percentile (15m):", regime.bbw_percentile_15m)
    
    await r.close()

if __name__ == "__main__":
    asyncio.run(main())
