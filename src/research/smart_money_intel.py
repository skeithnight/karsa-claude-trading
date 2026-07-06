"""Smart Money Intelligence — Whale/VC wallet tracking.

Deterministic scoring (0-100). Uses OnchainClient for wallet data.
Start with static seed wallet list. Auto-discover later.
"""

import asyncio
from src.utils.logging import get_logger

logger = get_logger("smart_money_intel")

# Seed VC/whale wallets (publicly known)
SEED_WALLETS = [
    {"address": "0x28C6c06298d514Db089934071355E5743bf21d60", "chain": "ethereum", "label": "Binance Hot Wallet", "type": "exchange"},
    {"address": "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549", "chain": "ethereum", "label": "Binance Cold Wallet", "type": "exchange"},
    {"address": "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d", "chain": "ethereum", "label": "Galaxy Digital", "type": "vc"},
    {"address": "0x8103683202aa8DA10536036EDef04CDd865A9025", "chain": "ethereum", "label": "Jump Trading", "type": "vc"},
]


class SmartMoneyIntelligence:
    """Smart money tracking and scoring."""

    def __init__(self, cache=None, onchain=None):
        self._cache = cache
        self._oc = onchain

    async def _ensure_clients(self):
        from src.data.onchain_client import OnchainClient
        if not self._oc:
            self._oc = OnchainClient(cache=self._cache)

    async def get_known_wallets(self) -> list[dict]:
        """Get tracked wallets (seed + DB-stored)."""
        wallets = list(SEED_WALLETS)
        # TODO: load additional wallets from smart_money_wallets table
        return wallets

    async def scan_wallet_activity(self, address: str, chain: str = "ethereum", days: int = 7) -> list[dict]:
        """Get recent token transfers for a wallet."""
        await self._ensure_clients()
        # Use token transfers endpoint with address filter
        try:
            session = await self._oc._get_session()
            base_url = self._oc._EXPLORER_URLS.get(chain) if hasattr(self._oc, '_EXPLORER_URLS') else None
            if not base_url:
                from src.data.onchain_client import _EXPLORER_URLS
                base_url = _EXPLORER_URLS.get(chain)
            if not base_url:
                return []

            from src.data.onchain_client import _EXPLORER_KEYS
            params = {
                "module": "account",
                "action": "tokentx",
                "address": address,
                "page": 1,
                "offset": 50,
                "sort": "desc",
            }
            api_key = _EXPLORER_KEYS.get(chain, "")
            if api_key:
                params["apikey"] = api_key

            async with session.get(base_url, params=params, timeout=asyncio.timeout(15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if data.get("status") != "1":
                    return []
                return data.get("result", [])[:50]
        except Exception as e:
            logger.warning("wallet_scan_failed", address=address[:10], error=str(e))
            return []

    def compute_score(self, metrics: dict) -> float:
        """Score 0-100 based on smart money signals."""
        score = 0.0

        # Number of tracked wallets accumulating (0-40)
        accumulating = metrics.get("accumulating_wallets", 0)
        score += min(40, accumulating * 10)

        # Net flow direction (0-30): positive = bullish
        net_flow = metrics.get("net_flow_usd", 0)
        if net_flow > 100000:
            score += 30
        elif net_flow > 10000:
            score += 20
        elif net_flow > 0:
            score += 10
        elif net_flow < -100000:
            score -= 10  # heavy selling is bearish

        # Transaction count (0-20): more activity = more interest
        tx_count = metrics.get("tx_count_7d", 0)
        score += min(20, tx_count * 2)

        # Diversity of wallets (0-10): different wallet types = stronger signal
        wallet_types = metrics.get("wallet_types_active", set())
        score += min(10, len(wallet_types) * 3)

        return round(max(0, min(100, score)), 2)

    async def detect_accumulation(self, symbol: str, contract: str | None = None, chain: str = "ethereum") -> dict:
        """Detect if smart money is accumulating a token."""
        wallets = await self.get_known_wallets()
        if not contract:
            return {"symbol": symbol, "score": 0, "error": "no_contract"}

        # Scan wallet activity for the token
        tasks = [self.scan_wallet_activity(w["address"], w["chain"]) for w in wallets[:5]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        accumulating = 0
        total_buys = 0
        total_sells = 0
        active_types = set()

        for wallet, result in zip(wallets[:5], results):
            if isinstance(result, Exception) or not result:
                continue

            buys = 0
            sells = 0
            for tx in (result or []):
                # Check if this tx involves our token contract
                if contract and tx.get("contractAddress", "").lower() == contract.lower():
                    if tx.get("to", "").lower() == wallet["address"].lower():
                        buys += 1
                    elif tx.get("from", "").lower() == wallet["address"].lower():
                        sells += 1

            if buys > sells:
                accumulating += 1
                active_types.add(wallet.get("type", "unknown"))
            total_buys += buys
            total_sells += sells

        metrics = {
            "accumulating_wallets": accumulating,
            "net_flow_usd": (total_buys - total_sells) * 1000,  # rough estimate
            "tx_count_7d": total_buys + total_sells,
            "wallet_types_active": active_types,
        }
        score = self.compute_score(metrics)

        return {
            "symbol": symbol,
            "score": score,
            "accumulating_wallets": accumulating,
            "total_buys": total_buys,
            "total_sells": total_sells,
            "metrics": metrics,
        }

    async def persist(self, symbol: str, metrics: dict):
        """Save smart money signal."""
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(
                text("""INSERT INTO research_audit_log
                    (token_symbol, action, details, agent)
                    VALUES (:symbol, 'smart_money_scan', :details, 'smart_money_intel')"""),
                {"symbol": symbol, "details": str(metrics)},
            )
            await session.commit()
