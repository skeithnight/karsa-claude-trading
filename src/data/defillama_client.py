"""Karsa Trading System — DeFiLlama API Client

Free DeFi analytics: TVL, protocol data, stablecoin flows, yields.
No API key required. Generous rate limits.

Base URL: https://api.llama.fi
"""

import asyncio
import time
from typing import Any

import aiohttp

from src.utils.logging import get_logger

logger = get_logger("defillama_client")

_BASE_URL = "https://api.llama.fi"
_CIRCUIT_BREAKER_TTL = 600
_MAX_FAILURES = 5
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]

_CACHE_TTL_PROTOCOLS = 600
_CACHE_TTL_TVL = 300
_CACHE_TTL_STABLECOINS = 600
_CACHE_TTL_YIELDS = 900


class DefiLlamaClient:
    """DeFiLlama API client — TVL, protocols, stablecoins, yields."""

    def __init__(self, cache=None):
        self._cache = cache
        self._session: aiohttp.ClientSession | None = None
        self._failures = 0
        self._blocked_until = 0.0
        self._last_request = 0.0
        self._min_interval = 0.5

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
            logger.warning("defillama_circuit_breaker_open")

    def _record_success(self):
        self._failures = 0

    async def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _cache_key(self, endpoint: str) -> str:
        return f"karsa:aode:dl:{endpoint}"

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

    async def _request(self, url: str) -> dict | list | None:
        if self._is_blocked():
            return None

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                await self._throttle()
                session = await self._get_session()
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
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
        logger.error("defillama_request_failed", url=url, error=last_error)
        return None

    async def get_protocols(self) -> list[dict]:
        """All protocols with TVL data (top 200)."""
        cache_key = self._cache_key("protocols")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"{_BASE_URL}/protocols")
        if not data:
            return []

        protocols = []
        for p in data[:200]:
            protocols.append({
                "name": p.get("name"),
                "slug": p.get("slug"),
                "symbol": p.get("symbol"),
                "chain": p.get("chain"),
                "chains": p.get("chains", []),
                "tvl": p.get("tvl"),
                "tvl_change_1d": p.get("change_1d"),
                "tvl_change_7d": p.get("change_7d"),
                "tvl_change_1m": p.get("change_1m"),
                "category": p.get("category"),
                "description": (p.get("description") or "")[:200],
                "url": p.get("url"),
                "github": p.get("github"),
                "audits": p.get("audits"),
                "audit_links": p.get("audit_links", []),
                "listed_at": p.get("listedAt"),
                "mcap": p.get("mcap"),
                "fdv": p.get("fdv"),
            })

        await self._set_cache(cache_key, protocols, _CACHE_TTL_PROTOCOLS)
        return protocols

    async def get_protocol_tvl(self, slug: str) -> dict | None:
        """Historical TVL for a specific protocol."""
        cache_key = self._cache_key(f"protocol:{slug}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"{_BASE_URL}/protocol/{slug}")
        if not data:
            return None

        tvl_history = data.get("tvl", [])
        result = {
            "name": data.get("name"),
            "slug": slug,
            "symbol": data.get("symbol"),
            "category": data.get("category"),
            "chains": data.get("chains", []),
            "current_tvl": tvl_history[-1].get("totalLiquidityUSD") if tvl_history else None,
            "tvl_history": [
                {"date": t.get("date"), "tvl": t.get("totalLiquidityUSD")}
                for t in tvl_history[-30:]
            ],
            "mcap": data.get("mcap"),
            "fdv": data.get("fdv"),
            "audits": data.get("audits"),
            "audit_links": data.get("audit_links", []),
            "url": data.get("url"),
            "github": data.get("github", []),
        }

        await self._set_cache(cache_key, result, _CACHE_TTL_TVL)
        return result

    async def get_chain_tvl(self, chain: str) -> dict | None:
        """TVL for a specific chain."""
        cache_key = self._cache_key(f"chain:{chain}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"{_BASE_URL}/v2/historicalChainTvl/{chain}")
        if not data or not isinstance(data, list):
            return None

        result = {
            "chain": chain,
            "current_tvl": data[-1].get("tvl") if data else None,
            "tvl_history": [
                {"date": d.get("date"), "tvl": d.get("tvl")}
                for d in data[-30:]
            ],
        }

        await self._set_cache(cache_key, result, _CACHE_TTL_TVL)
        return result

    async def get_stablecoins(self) -> list[dict]:
        """Stablecoin data: circulating supply, chains, peg."""
        cache_key = self._cache_key("stablecoins")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"{_BASE_URL}/stablecoins")
        if not data:
            return []

        stables = []
        for s in data.get("peggedAssets", [])[:30]:
            stables.append({
                "name": s.get("name"),
                "symbol": s.get("symbol"),
                "circulating": s.get("circulating", {}).get("peggedUSD"),
                "chain_circulating": s.get("chainCirculating"),
                "price": s.get("price"),
                "peg_type": s.get("pegType"),
            })

        await self._set_cache(cache_key, stables, _CACHE_TTL_STABLECOINS)
        return stables

    async def get_top_protocols_by_chain(self, chain: str, n: int = 20) -> list[dict]:
        """Top protocols on a specific chain by TVL."""
        protocols = await self.get_protocols()
        chain_lower = chain.lower()
        chain_protocols = [
            p for p in protocols
            if chain_lower in [c.lower() for c in p.get("chains", [])]
        ]
        chain_protocols.sort(key=lambda x: x.get("tvl") or 0, reverse=True)
        return chain_protocols[:n]

    async def get_yields(self, protocol: str | None = None) -> list[dict]:
        """Yield/pool data, optionally filtered by protocol."""
        cache_key = self._cache_key(f"yields:{protocol or 'all'}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"{_BASE_URL}/pools")
        if not data:
            return []

        pools = data.get("data", [])
        if protocol:
            pools = [p for p in pools if p.get("project", "").lower() == protocol.lower()]

        result = []
        for p in pools[:50]:
            result.append({
                "pool": p.get("pool"),
                "chain": p.get("chain"),
                "project": p.get("project"),
                "symbol": p.get("symbol"),
                "tvl": p.get("tvlUsd"),
                "apy": p.get("apy"),
                "apy_base": p.get("apyBase"),
                "apy_reward": p.get("apyReward"),
                "il_7d": p.get("il7d"),
                "exposure": p.get("exposure"),
            })

        await self._set_cache(cache_key, result, _CACHE_TTL_YIELDS)
        return result
