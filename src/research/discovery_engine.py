"""Karsa Trading System — AODE Discovery Engine

Multi-source token discovery pipeline:
  - CoinGecko: trending, top gainers, new listings
  - DeFiLlama: new protocols, TVL spikes
  - DexScreener: new DEX pairs, trending
  - Bybit: new perpetual listings

Deduplicates across sources, filters by minimum thresholds,
persists to discovered_tokens table.

Feature-flagged: aode_discovery_enabled
"""

import asyncio
from datetime import datetime, timezone

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("discovery_engine")

# Minimum thresholds for discovery
_MIN_VOLUME_USD = 100_000
_MIN_LIQUIDITY_USD = 50_000
_MIN_MARKET_CAP_USD = 500_000
_MAX_MARKET_CAP_USD = 50_000_000_000  # exclude mega-caps already in trading universe


class DiscoveryEngine:
    """Multi-source token discovery engine."""

    def __init__(self, cache=None, bybit_client=None):
        self._cache = cache
        self._bybit = bybit_client

    async def discover(self) -> list[dict]:
        """Run full discovery cycle across all sources. Returns deduplicated tokens."""
        from src.data.defillama_client import DefiLlamaClient
        from src.data.dexscreener_client import DexScreenerClient

        dl = DefiLlamaClient(cache=self._cache)
        ds = DexScreenerClient(cache=self._cache)

        try:
            results = await asyncio.gather(
                self._scan_defillama(dl),
                self._scan_dexscreener(ds),
                self._scan_bybit_new_listings(),
                return_exceptions=True,
            )
        finally:
            await asyncio.gather(dl.close(), ds.close(), return_exceptions=True)

        all_tokens = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("discovery_source_failed", source=i, error=str(result))
                continue
            all_tokens.extend(result)

        deduplicated = self._deduplicate(all_tokens)
        filtered = self._filter_tokens(deduplicated)

        logger.info("discovery_cycle_complete",
                     total_raw=len(all_tokens),
                     after_dedup=len(deduplicated),
                     after_filter=len(filtered))

        return filtered

    async def _scan_defillama(self, dl) -> list[dict]:
        """DeFiLlama: protocols with significant TVL changes."""
        tokens = []
        protocols = await dl.get_protocols()

        for p in protocols:
            tvl_change_7d = p.get("tvl_change_7d") or 0
            # Flag protocols with >30% TVL growth in 7d
            if abs(tvl_change_7d) > 30:
                symbol = p.get("symbol") or ""
                if not symbol or symbol == "-":
                    continue
                tokens.append({
                    "symbol": symbol.upper(),
                    "name": p.get("name"),
                    "source": "defillama_tvl_spike",
                    "chain": p.get("chain", "multi"),
                    "market_cap_usd": p.get("mcap"),
                    "tvl_usd": p.get("tvl"),
                    "tvl_change_7d_pct": tvl_change_7d,
                    "category": p.get("category"),
                    "defillama_slug": p.get("slug"),
                })

        return tokens

    async def _scan_dexscreener(self, ds) -> list[dict]:
        """DexScreener: new pairs with decent liquidity."""
        tokens = []
        pairs = await ds.get_new_pairs()

        for p in pairs:
            chain = p.get("chain", "")
            symbol = p.get("base_token", "")
            if not symbol:
                continue
            tokens.append({
                "symbol": symbol.upper(),
                "name": p.get("name") or symbol,
                "source": "dexscreener_new",
                "chain": chain,
                "contract_address": p.get("token_address"),
            })

        return tokens

    async def _scan_bybit_new_listings(self) -> list[dict]:
        """Bybit: recently listed perpetuals not in current universe."""
        if not self._bybit:
            return []

        tokens = []
        try:
            from src.advisory.crypto_universe import CRYPTO_UNIVERSE
            existing = set(CRYPTO_UNIVERSE)

            all_perps = await self._bybit.get_all_perps(min_volume_usd=_MIN_VOLUME_USD)
            for p in all_perps:
                symbol = p.get("symbol", "")
                if symbol and symbol not in existing:
                    tokens.append({
                        "symbol": symbol,
                        "name": symbol.replace("USDT", ""),
                        "source": "bybit_new_listing",
                        "chain": "bybit_perp",
                        "volume_24h_usd": p.get("volume_24h_usd"),
                        "price_usd": p.get("price"),
                        "price_change_24h_pct": p.get("change_pct"),
                    })
        except Exception as e:
            logger.warning("bybit_discovery_failed", error=str(e))

        return tokens

    def _deduplicate(self, tokens: list[dict]) -> list[dict]:
        """Deduplicate by symbol, merge data from multiple sources."""
        seen: dict[str, dict] = {}
        for t in tokens:
            sym = t.get("symbol", "").upper()
            if not sym:
                continue
            if sym in seen:
                # Merge: keep richer data, add source
                existing = seen[sym]
                existing_sources = existing.get("_sources", {existing["source"]})
                existing_sources.add(t["source"])
                existing["_sources"] = existing_sources
                # Fill in missing fields
                for key in ("market_cap_usd", "volume_24h_usd", "liquidity_usd",
                            "price_usd", "price_change_24h_pct", "coingecko_id",
                            "defillama_slug", "contract_address", "category"):
                    if key in t and key not in existing:
                        existing[key] = t[key]
            else:
                t["_sources"] = {t["source"]}
                seen[sym] = t
        return list(seen.values())

    def _filter_tokens(self, tokens: list[dict]) -> list[dict]:
        """Filter by minimum quality thresholds."""
        filtered = []
        for t in tokens:
            sources = t.get("_sources", set())
            # Tokens from multiple sources always pass
            if len(sources) >= 2:
                t.pop("_sources", None)
                filtered.append(t)
                continue

            # Single source: apply thresholds
            mcap = t.get("market_cap_usd") or 0
            vol = t.get("volume_24h_usd") or 0
            liq = t.get("liquidity_usd") or 0

            # Skip mega-caps (already in trading universe)
            if mcap > _MAX_MARKET_CAP_USD:
                continue

            # Trending/coingecko: always include (ranked by popularity)
            if t["source"] in ("coingecko_trending", "coingecko_mover"):
                t.pop("_sources", None)
                filtered.append(t)
                continue

            # Others: need minimum volume or liquidity
            if vol >= _MIN_VOLUME_USD or liq >= _MIN_LIQUIDITY_USD:
                t.pop("_sources", None)
                filtered.append(t)

        return filtered

    async def persist_discoveries(self, tokens: list[dict]) -> int:
        """Persist discovered tokens to database. Returns count of new inserts."""
        from src.models.database import async_session
        from sqlalchemy import text

        inserted = 0
        async with async_session() as session:
            for t in tokens:
                try:
                    await session.execute(
                        text("""
                            INSERT INTO discovered_tokens
                                (symbol, chain, contract_address, source, name,
                                 market_cap_usd, volume_24h_usd, liquidity_usd,
                                 price_usd, price_change_24h_pct, fdv)
                            VALUES
                                (:symbol, :chain, :contract_address, :source, :name,
                                 :market_cap_usd, :volume_24h_usd, :liquidity_usd,
                                 :price_usd, :price_change_24h_pct, :fdv)
                            ON CONFLICT (symbol, chain, COALESCE(contract_address, ''))
                            DO UPDATE SET
                                last_updated_at = NOW(),
                                price_usd = EXCLUDED.price_usd,
                                price_change_24h_pct = EXCLUDED.price_change_24h_pct,
                                volume_24h_usd = EXCLUDED.volume_24h_usd,
                                market_cap_usd = EXCLUDED.market_cap_usd
                        """),
                        {
                            "symbol": t.get("symbol", ""),
                            "chain": t.get("chain", "multi"),
                            "contract_address": t.get("contract_address"),
                            "source": t.get("source", "unknown"),
                            "name": t.get("name"),
                            "market_cap_usd": t.get("market_cap_usd"),
                            "volume_24h_usd": t.get("volume_24h_usd"),
                            "liquidity_usd": t.get("liquidity_usd"),
                            "price_usd": t.get("price_usd"),
                            "price_change_24h_pct": t.get("price_change_24h_pct"),
                            "fdv": t.get("fdv"),
                        },
                    )
                    inserted += 1
                except Exception as e:
                    logger.warning("persist_token_failed", symbol=t.get("symbol"), error=str(e))

            await session.commit()

        logger.info("discovery_persisted", count=inserted)
        return inserted
