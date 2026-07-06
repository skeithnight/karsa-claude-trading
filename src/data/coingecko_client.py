"""Karsa Trading System — CoinGecko API Client

Free-tier crypto market data: trending, top coins, coin details, categories.
Follows BybitClient patterns: circuit breaker, Redis caching, retry, rate limiting.

Rate limits: ~10-30 calls/min (free tier). No API key required for basic endpoints.
"""

import asyncio
import time
from typing import Any

import aiohttp

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("coingecko_client")

_BASE_URL = "https://api.coingecko.com/api/v3"
_CIRCUIT_BREAKER_TTL = 600
_MAX_FAILURES = 5
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]

# Cache TTLs (seconds)
_CACHE_TTL_TRENDING = 300
_CACHE_TTL_MARKETS = 120
_CACHE_TTL_DETAIL = 600
_CACHE_TTL_GLOBAL = 600
_CACHE_TTL_CATEGORIES = 900


class CoinGeckoClient:
    """CoinGecko free API client with circuit breaker and caching."""

    def __init__(self, cache=None):
        self._cache = cache
        self._session: aiohttp.ClientSession | None = None
        self._failures = 0
        self._blocked_until = 0.0
        self._last_request = 0.0
        self._min_interval = 2.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"accept": "application/json"}
            if settings.COINGECKO_API_KEY:
                headers["x-cg-demo-api-key"] = settings.COINGECKO_API_KEY
            self._session = aiohttp.ClientSession(headers=headers)
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
            logger.warning("coingecko_circuit_breaker_open", failures=self._failures)

    def _record_success(self):
        self._failures = 0

    async def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _cache_key(self, endpoint: str) -> str:
        return f"karsa:aode:cg:{endpoint}"

    async def _get_cache(self, key: str) -> dict | list | None:
        if not self._cache:
            return None
        try:
            import json
            raw = await self._cache.get(key)
            if raw:
                return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass
        return None

    async def _set_cache(self, key: str, data: Any, ttl: int):
        if not self._cache:
            return
        try:
            import json
            await self._cache.set(key, json.dumps(data), ttl)
        except Exception:
            pass

    async def _request(self, path: str, params: dict | None = None) -> dict | list | None:
        """Request with retry, circuit breaker."""
        if self._is_blocked():
            return None

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                await self._throttle()
                session = await self._get_session()
                url = f"{_BASE_URL}{path}"
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        self._record_success()
                        return await resp.json()
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        logger.warning("coingecko_rate_limited", retry_after=retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    last_error = f"HTTP {resp.status}"
                    if resp.status in (400, 401, 403):
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = str(e)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])

        self._record_failure()
        logger.error("coingecko_request_failed", path=path, error=last_error)
        return None

    async def get_trending(self) -> list[dict]:
        """Trending coins (searched most in last 24h)."""
        cache_key = self._cache_key("trending")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request("/search/trending")
        if not data:
            return []

        coins = []
        for item in data.get("coins", []):
            coin = item.get("item", {})
            coins.append({
                "id": coin.get("id"),
                "symbol": coin.get("symbol"),
                "name": coin.get("name"),
                "market_cap_rank": coin.get("market_cap_rank"),
                "price_btc": coin.get("price_btc"),
                "score": coin.get("score"),
            })

        await self._set_cache(cache_key, coins, _CACHE_TTL_TRENDING)
        return coins

    async def get_top_coins(self, n: int = 100, vs_currency: str = "usd") -> list[dict]:
        """Top coins by market cap."""
        cache_key = self._cache_key(f"markets:{n}:{vs_currency}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request("/coins/markets", {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": min(n, 250),
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h,7d,30d",
        })
        if not data:
            return []

        coins = []
        for c in data:
            coins.append({
                "id": c.get("id"),
                "symbol": c.get("symbol", "").upper(),
                "name": c.get("name"),
                "market_cap": c.get("market_cap"),
                "market_cap_rank": c.get("market_cap_rank"),
                "price": c.get("current_price"),
                "volume_24h": c.get("total_volume"),
                "price_change_24h_pct": c.get("price_change_percentage_24h"),
                "price_change_7d_pct": c.get("price_change_percentage_7d_in_currency"),
                "price_change_30d_pct": c.get("price_change_percentage_30d_in_currency"),
                "ath": c.get("ath"),
                "ath_change_pct": c.get("ath_change_percentage"),
                "circulating_supply": c.get("circulating_supply"),
                "total_supply": c.get("total_supply"),
                "max_supply": c.get("max_supply"),
                "fdv": c.get("fully_diluted_valuation"),
            })

        await self._set_cache(cache_key, coins, _CACHE_TTL_MARKETS)
        return coins[:n]

    async def get_coin_detail(self, coin_id: str) -> dict | None:
        """Detailed coin info: team, links, community, developer data."""
        cache_key = self._cache_key(f"detail:{coin_id}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"/coins/{coin_id}", {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "true",
            "developer_data": "true",
            "sparkline": "false",
        })
        if not data:
            return None

        result = {
            "id": data.get("id"),
            "symbol": data.get("symbol", "").upper(),
            "name": data.get("name"),
            "categories": data.get("categories", []),
            "description": data.get("description", {}).get("en", ""),
            "links": {
                "homepage": data.get("links", {}).get("homepage", [None])[0],
                "twitter": data.get("links", {}).get("twitter_screen_name"),
                "telegram": data.get("links", {}).get("telegram_channel_identifier"),
                "subreddit": data.get("links", {}).get("subreddit_url"),
                "github": data.get("links", {}).get("repos_url", {}).get("github", []),
            },
            "market_data": {
                "market_cap": data.get("market_data", {}).get("market_cap", {}).get("usd"),
                "volume_24h": data.get("market_data", {}).get("total_volume", {}).get("usd"),
                "fdv": data.get("market_data", {}).get("fully_diluted_valuation", {}).get("usd"),
                "circulating_supply": data.get("market_data", {}).get("circulating_supply"),
                "total_supply": data.get("market_data", {}).get("total_supply"),
                "max_supply": data.get("market_data", {}).get("max_supply"),
                "ath": data.get("market_data", {}).get("ath", {}).get("usd"),
                "atl": data.get("market_data", {}).get("atl", {}).get("usd"),
                "price_change_24h_pct": data.get("market_data", {}).get("price_change_percentage_24h"),
                "price_change_7d_pct": data.get("market_data", {}).get("price_change_percentage_7d"),
                "price_change_30d_pct": data.get("market_data", {}).get("price_change_percentage_30d"),
            },
            "community": {
                "twitter_followers": data.get("community_data", {}).get("twitter_followers"),
                "reddit_subscribers": data.get("community_data", {}).get("reddit_subscribers"),
                "reddit_avg_posts_48h": data.get("community_data", {}).get("reddit_average_posts_48h"),
                "telegram_members": data.get("community_data", {}).get("telegram_channel_user_count"),
            },
            "developer": {
                "github_forks": data.get("developer_data", {}).get("forks"),
                "github_stars": data.get("developer_data", {}).get("stars"),
                "github_subscribers": data.get("developer_data", {}).get("subscribers"),
                "github_total_issues": data.get("developer_data", {}).get("total_issues"),
                "github_closed_issues": data.get("developer_data", {}).get("closed_issues"),
                "github_prs_merged": data.get("developer_data", {}).get("pull_requests_merged"),
                "github_pr_contributors": data.get("developer_data", {}).get("pull_request_contributors"),
                "commit_count_4_weeks": data.get("developer_data", {}).get("commit_count_4_weeks"),
                "code_additions_4w": data.get("developer_data", {}).get("code_additions_deletions_4_weeks", {}).get("additions"),
                "code_deletions_4w": data.get("developer_data", {}).get("code_additions_deletions_4_weeks", {}).get("deletions"),
            },
            "genesis_date": data.get("genesis_date"),
            "hashing_algorithm": data.get("hashing_algorithm"),
            "sentiment_up_pct": data.get("sentiment_votes_up_percentage"),
            "sentiment_down_pct": data.get("sentiment_votes_down_percentage"),
        }

        await self._set_cache(cache_key, result, _CACHE_TTL_DETAIL)
        return result

    async def get_global_data(self) -> dict | None:
        """Global crypto market data: total mcap, BTC dominance, active cryptos."""
        cache_key = self._cache_key("global")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request("/global")
        if not data:
            return None

        gd = data.get("data", {})
        result = {
            "total_market_cap_usd": gd.get("total_market_cap", {}).get("usd"),
            "total_volume_24h_usd": gd.get("total_volume", {}).get("usd"),
            "btc_dominance": gd.get("market_cap_percentage", {}).get("btc"),
            "eth_dominance": gd.get("market_cap_percentage", {}).get("eth"),
            "active_cryptos": gd.get("active_cryptocurrencies"),
            "markets": gd.get("markets"),
            "market_cap_change_24h_pct": gd.get("market_cap_change_percentage_24h_usd"),
        }

        await self._set_cache(cache_key, result, _CACHE_TTL_GLOBAL)
        return result

    async def get_categories(self) -> list[dict]:
        """Crypto categories with market cap and volume."""
        cache_key = self._cache_key("categories")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request("/coins/categories", {"order": "market_cap_desc"})
        if not data:
            return []

        categories = []
        for cat in data[:50]:
            categories.append({
                "id": cat.get("id"),
                "name": cat.get("name"),
                "market_cap": cat.get("market_cap"),
                "volume_24h": cat.get("volume_24h"),
                "market_cap_change_24h_pct": cat.get("market_cap_change_24h"),
                "top_3_coins": cat.get("top_3_coins_id", []),
                "updated_at": cat.get("updated_at"),
            })

        await self._set_cache(cache_key, categories, _CACHE_TTL_CATEGORIES)
        return categories

    async def search(self, query: str) -> list[dict]:
        """Search coins by name/symbol."""
        data = await self._request("/search", {"query": query})
        if not data:
            return []

        return [
            {
                "id": c.get("id"),
                "symbol": c.get("symbol"),
                "name": c.get("name"),
                "market_cap_rank": c.get("market_cap_rank"),
            }
            for c in data.get("coins", [])[:20]
        ]
