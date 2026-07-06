"""Karsa Trading System — DexScreener API Client

DEX pair discovery, trending tokens, new pairs, liquidity data.
No API key required. Free tier: 300 req/min.

Base URL: https://api.dexscreener.com
"""

import asyncio
import time
from typing import Any

import aiohttp

from src.utils.logging import get_logger

logger = get_logger("dexscreener_client")

_BASE_URL = "https://api.dexscreener.com"
_CIRCUIT_BREAKER_TTL = 600
_MAX_FAILURES = 5
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]


class DexScreenerClient:
    """DexScreener API client — DEX pairs, trending, new listings."""

    def __init__(self, cache=None):
        self._cache = cache
        self._session: aiohttp.ClientSession | None = None
        self._failures = 0
        self._blocked_until = 0.0
        self._last_request = 0.0
        self._min_interval = 0.2

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"accept": "application/json"})
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _is_blocked(self) -> bool:
        return time.time() < self._blocked_until

    def _record_failure(self):
        self._failures += 1
        if self._failures >= _MAX_FAILURES:
            self._blocked_until = time.time() + _CIRCUIT_BREAKER_TTL
            logger.warning("dexscreener_circuit_breaker_open")

    def _record_success(self):
        self._failures = 0

    async def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _cache_key(self, endpoint: str) -> str:
        return f"karsa:aode:ds:{endpoint}"

    async def _get_cache(self, key: str) -> Any:
        if not self._cache:
            return None
        try:
            import json
            raw = await self._cache.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _set_cache(self, key: str, data: Any, ttl: int):
        if not self._cache:
            return
        try:
            import json
            await self._cache.set(key, json.dumps(data), ttl)
        except Exception:
            pass

    async def _request(self, path: str) -> dict | list | None:
        if self._is_blocked():
            return None

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                await self._throttle()
                session = await self._get_session()
                async with session.get(f"{_BASE_URL}{path}", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        self._record_success()
                        return await resp.json()
                    last_error = f"HTTP {resp.status}"
                    if resp.status in (400, 401, 403):
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = str(e)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])

        self._record_failure()
        logger.error("dexscreener_request_failed", path=path, error=last_error)
        return None

    def _normalize_pair(self, pair: dict) -> dict:
        return {
            "pair_address": pair.get("pairAddress"),
            "base_token": pair.get("baseToken", {}).get("symbol"),
            "base_token_address": pair.get("baseToken", {}).get("address"),
            "quote_token": pair.get("quoteToken", {}).get("symbol"),
            "chain": pair.get("chainId"),
            "dex": pair.get("dexId"),
            "price_usd": pair.get("priceUsd"),
            "price_change_24h_pct": pair.get("priceChange", {}).get("h24"),
            "volume_24h_usd": pair.get("volume", {}).get("h24"),
            "liquidity_usd": pair.get("liquidity", {}).get("usd"),
            "fdv": pair.get("fdv"),
            "market_cap": pair.get("marketCap"),
            "pair_created_at": pair.get("pairCreatedAt"),
            "url": pair.get("url"),
        }

    async def get_new_pairs(self, chain: str | None = None) -> list[dict]:
        """Latest new pairs, optionally filtered by chain."""
        cache_key = self._cache_key(f"new_pairs:{chain or 'all'}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request("/token-profiles/latest/v1")
        if not data or not isinstance(data, list):
            return []

        results = []
        for item in data[:50]:
            if chain and item.get("chainId", "").lower() != chain.lower():
                continue
            results.append({
                "token_address": item.get("tokenAddress"),
                "chain": item.get("chainId"),
                "name": (item.get("description") or "")[:100],
                "url": item.get("url"),
                "links": item.get("links", []),
            })

        await self._set_cache(cache_key, results, 120)
        return results

    async def search_pairs(self, query: str) -> list[dict]:
        """Search pairs by token name/symbol."""
        data = await self._request(f"/latest/dex/search?q={query}")
        if not data:
            return []
        pairs = data.get("pairs", []) if isinstance(data, dict) else data
        return [self._normalize_pair(p) for p in (pairs or [])[:30]]

    async def get_token_pairs(self, token_address: str, chain: str = "ethereum") -> list[dict]:
        """All pairs for a specific token address."""
        data = await self._request(f"/tokens/v1/{chain}/{token_address}")
        if not data or not isinstance(data, list):
            return []
        return [self._normalize_pair(p) for p in data[:20]]

    async def get_trending(self) -> list[dict]:
        """Trending pairs (proxy via token profiles)."""
        cache_key = self._cache_key("trending")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request("/token-profiles/latest/v1")
        if not data or not isinstance(data, list):
            return []

        results = [{"token_address": i.get("tokenAddress"), "chain": i.get("chainId"), "url": i.get("url")} for i in data[:30]]
        await self._set_cache(cache_key, results, 180)
        return results
