"""On-chain Intelligence — holder metrics, TVL, DEX volume, exchange flows.

Deterministic scoring (0-100). No LLM calls.
Uses: DefiLlamaClient, OnchainClient, DexScreenerClient.
"""

from src.utils.logging import get_logger

logger = get_logger("onchain_intel")


class OnchainIntelligence:
    """On-chain data collection and scoring."""

    def __init__(self, cache=None, defillama=None, onchain=None, dexscreener=None):
        self._cache = cache
        self._dl = defillama
        self._oc = onchain
        self._ds = dexscreener

    async def _ensure_clients(self):
        from src.data.defillama_client import DefiLlamaClient
        from src.data.onchain_client import OnchainClient
        from src.data.dexscreener_client import DexScreenerClient
        if not self._dl:
            self._dl = DefiLlamaClient(cache=self._cache)
        if not self._oc:
            self._oc = OnchainClient(cache=self._cache)
        if not self._ds:
            self._ds = DexScreenerClient(cache=self._cache)

    async def get_tvl(self, slug: str) -> dict | None:
        await self._ensure_clients()
        return await self._dl.get_protocol_tvl(slug)

    async def get_dex_volume(self, symbol: str) -> float | None:
        await self._ensure_clients()
        pairs = await self._ds.search_pairs(symbol)
        if not pairs:
            return None
        total_vol = sum(p.get("volume_24h_usd") or 0 for p in pairs)
        return total_vol

    async def get_holder_data(self, contract: str, chain: str = "ethereum") -> dict:
        await self._ensure_clients()
        if chain == "solana":
            holders = await self._oc.get_solana_token_holders(contract)
            return {
                "holder_count": len(holders),
                "top10_pct": None,  # needs total supply for calc
                "holders": holders[:10],
            }
        contract_info = await self._oc.get_contract_info(contract, chain)
        transfers = await self._oc.get_token_transfers(contract, chain, days=7)
        unique_wallets = set()
        for tx in transfers:
            unique_wallets.add(tx.get("from", ""))
            unique_wallets.add(tx.get("to", ""))
        unique_wallets.discard("")
        return {
            "contract_verified": (contract_info or {}).get("verified", False),
            "is_proxy": (contract_info or {}).get("proxy", False),
            "unique_wallets_7d": len(unique_wallets),
            "transfer_count_7d": len(transfers),
        }

    async def get_liquidity(self, symbol: str) -> dict:
        await self._ensure_clients()
        pairs = await self._ds.search_pairs(symbol)
        if not pairs:
            return {"total_liquidity_usd": 0, "pair_count": 0}
        total_liq = sum(p.get("liquidity_usd") or 0 for p in pairs)
        return {
            "total_liquidity_usd": total_liq,
            "pair_count": len(pairs),
            "top_pair": pairs[0] if pairs else None,
        }

    def compute_score(self, metrics: dict) -> float:
        """Score 0-100 based on on-chain health."""
        score = 0.0

        # TVL component (0-30): log scale, $10M+ = 30
        import math
        tvl = metrics.get("tvl_usd") or 0
        if tvl > 0:
            score += min(30, (math.log10(max(tvl, 1)) / 7) * 30)

        # DEX volume (0-25): $1M+ daily = 25
        vol = metrics.get("dex_volume_24h_usd") or 0
        if vol > 0:
            score += min(25, (math.log10(max(vol, 1)) / 6) * 25)

        # Liquidity (0-20): $500K+ = 20
        liq = metrics.get("liquidity_usd") or 0
        if liq > 0:
            score += min(20, (math.log10(max(liq, 1)) / 5.7) * 20)

        # Holder growth (0-15): 1000+ unique wallets = 15
        wallets = metrics.get("unique_wallets_7d") or 0
        score += min(15, (wallets / 1000) * 15)

        # Contract verified (0-10)
        if metrics.get("contract_verified"):
            score += 10

        return round(min(100, score), 2)

    async def snapshot(self, symbol: str, chain: str = "multi") -> dict:
        """Collect all on-chain data and compute score."""
        await self._ensure_clients()
        import asyncio
        tvl_data, dex_vol, liquidity = await asyncio.gather(
            self._dl.get_protocols(),  # will filter below
            self.get_dex_volume(symbol),
            self.get_liquidity(symbol),
            return_exceptions=True,
        )

        tvl_usd = 0
        if isinstance(tvl_data, list):
            for p in tvl_data:
                if p.get("symbol", "").upper() == symbol.upper():
                    tvl_usd = p.get("tvl") or 0
                    break

        metrics = {
            "symbol": symbol,
            "chain": chain,
            "tvl_usd": tvl_usd if not isinstance(tvl_data, Exception) else 0,
            "dex_volume_24h_usd": dex_vol if not isinstance(dex_vol, Exception) else 0,
            "liquidity_usd": (liquidity.get("total_liquidity_usd", 0) if isinstance(liquidity, dict) else 0),
            "unique_wallets_7d": 0,
            "contract_verified": False,
        }
        metrics["score"] = self.compute_score(metrics)
        return metrics

    async def persist(self, symbol: str, chain: str, metrics: dict):
        """Save snapshot to onchain_snapshots table."""
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(
                text("""INSERT INTO onchain_snapshots
                    (symbol, chain, holder_count, top10_holder_pct, tvl_usd,
                     dex_volume_24h_usd, liquidity_usd, unique_wallets_24h, transaction_count_24h)
                    VALUES (:symbol, :chain, :holders, :top10, :tvl, :dex_vol, :liq, :wallets, :txs)"""),
                {
                    "symbol": symbol, "chain": chain,
                    "holders": metrics.get("holder_count"),
                    "top10": metrics.get("top10_pct"),
                    "tvl": metrics.get("tvl_usd"),
                    "dex_vol": metrics.get("dex_volume_24h_usd"),
                    "liq": metrics.get("liquidity_usd"),
                    "wallets": metrics.get("unique_wallets_7d"),
                    "txs": metrics.get("transfer_count_7d"),
                },
            )
            await session.commit()
