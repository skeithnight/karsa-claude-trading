"""Contract Risk Intelligence — Smart contract security, rug detection, tokenomics.

Deterministic scoring (0-100). Higher = riskier (inverted from other modules).
Uses: OnchainClient, DefiLlamaClient, CoinGeckoClient.
"""

from src.utils.logging import get_logger

logger = get_logger("risk_intel")


class RiskIntelligence:
    """Contract and tokenomics risk assessment."""

    def __init__(self, cache=None, onchain=None, defillama=None, coingecko=None):
        self._cache = cache
        self._oc = onchain
        self._dl = defillama
        self._cg = coingecko

    async def _ensure_clients(self):
        from src.data.onchain_client import OnchainClient
        from src.data.defillama_client import DefiLlamaClient
        from src.data.coingecko_client import CoinGeckoClient
        if not self._oc:
            self._oc = OnchainClient(cache=self._cache)
        if not self._dl:
            self._dl = DefiLlamaClient(cache=self._cache)
        if not self._cg:
            self._cg = CoinGeckoClient(cache=self._cache)

    async def close(self):
        """Close all underlying HTTP clients to prevent connection leaks."""
        for client in (self._oc, self._dl, self._cg):
            if client and hasattr(client, 'close'):
                await client.close()

    async def check_contract_risk(self, contract: str, chain: str = "ethereum") -> dict:
        """Check smart contract verification, proxy pattern, ownership."""
        await self._ensure_clients()
        info = await self._oc.get_contract_info(contract, chain)
        if not info:
            return {"verified": False, "proxy": False, "risk_score": 80}

        risk = 0
        # Unverified contract = high risk
        if not info.get("verified"):
            risk += 40
        # Proxy contract = medium risk (can be upgraded)
        if info.get("proxy"):
            risk += 20
        # Not a contract at all = suspicious
        if not info.get("is_contract"):
            risk += 30

        return {
            "verified": info.get("verified", False),
            "proxy": info.get("proxy", False),
            "implementation": info.get("implementation"),
            "contract_name": info.get("contract_name"),
            "risk_score": min(100, risk),
        }

    async def check_rug_indicators(self, symbol: str, coingecko_id: str | None = None) -> dict:
        """Check rug pull indicators: liquidity, holder concentration, age."""
        await self._ensure_clients()
        indicators = {
            "liquidity_locked": None,
            "holder_concentration": None,
            "project_age_days": None,
            "audit_status": "unknown",
            "risk_score": 50,  # default medium risk
        }

        if not coingecko_id:
            return indicators

        detail = await self._cg.get_coin_detail(coingecko_id)
        if not detail:
            return indicators

        # Check project age from genesis date
        genesis = detail.get("genesis_date")
        if genesis:
            from datetime import datetime
            try:
                gen_date = datetime.fromisoformat(genesis)
                age_days = (datetime.utcnow() - gen_date).days
                indicators["project_age_days"] = age_days
                if age_days < 30:
                    indicators["risk_score"] += 20  # very new = riskier
                elif age_days > 365:
                    indicators["risk_score"] -= 15  # established = safer
            except (ValueError, TypeError):
                pass

        # Check audit status from DeFiLlama
        protocols = await self._dl.get_protocols()
        for p in protocols:
            if p.get("symbol", "").upper() == symbol.upper():
                audits = p.get("audits")
                if audits and audits != "0":
                    indicators["audit_status"] = "audited"
                    indicators["risk_score"] -= 20
                audit_links = p.get("audit_links") or []
                if audit_links:
                    indicators["audit_links"] = audit_links
                break

        # Market cap vs FDV ratio (tokenomics risk)
        market_data = detail.get("market_data", {})
        mcap = market_data.get("market_cap") or 0
        fdv = market_data.get("fdv") or 0
        if fdv > 0 and mcap > 0:
            ratio = mcap / fdv
            if ratio < 0.1:  # <10% circulating = high dilution risk
                indicators["risk_score"] += 25
            elif ratio < 0.3:
                indicators["risk_score"] += 10
            indicators["circulating_ratio"] = round(ratio, 4)

        # Supply concentration (from circulating vs total)
        circ = market_data.get("circulating_supply") or 0
        total = market_data.get("total_supply") or 0
        if total > 0 and circ > 0:
            indicators["circulating_pct"] = round((circ / total) * 100, 2)

        indicators["risk_score"] = max(0, min(100, indicators["risk_score"]))
        return indicators

    async def analyze_tokenomics(self, coingecko_id: str) -> dict:
        """Analyze tokenomics: supply distribution, inflation, unlocks."""
        await self._ensure_clients()
        detail = await self._cg.get_coin_detail(coingecko_id)
        if not detail:
            return {"score": 50, "error": "no_data"}

        market_data = detail.get("market_data", {})
        circ = market_data.get("circulating_supply") or 0
        total = market_data.get("total_supply") or 0
        max_sup = market_data.get("max_supply") or 0

        score = 50  # neutral

        # Circulating/total ratio
        if total > 0 and circ > 0:
            ratio = circ / total
            if ratio > 0.8:
                score += 20  # most tokens circulating = good
            elif ratio > 0.5:
                score += 10
            elif ratio < 0.2:
                score -= 20  # heavy unlock risk

        # Has max supply cap = deflationary potential
        if max_sup and max_sup > 0:
            score += 10

        # FDV vs mcap
        mcap = market_data.get("market_cap") or 0
        fdv = market_data.get("fdv") or 0
        if fdv > 0 and mcap > 0:
            if fdv / mcap > 5:
                score -= 15  # massive dilution overhang
            elif fdv / mcap < 1.5:
                score += 10  # low dilution risk

        return {
            "circulating_supply": circ,
            "total_supply": total,
            "max_supply": max_sup,
            "mcap": mcap,
            "fdv": fdv,
            "score": max(0, min(100, score)),
        }

    def compute_score(self, metrics: dict) -> float:
        """Compute overall risk score (0-100). Lower = safer."""
        contract_risk = metrics.get("contract_risk_score", 50)
        rug_risk = metrics.get("rug_risk_score", 50)
        tokenomics = metrics.get("tokenomics_score", 50)

        # Weighted average (inverted: lower is better)
        raw = (contract_risk * 0.4) + (rug_risk * 0.35) + ((100 - tokenomics) * 0.25)
        return round(min(100, max(0, raw)), 2)

    async def full_assessment(self, symbol: str, contract: str | None = None,
                               chain: str = "ethereum", coingecko_id: str | None = None) -> dict:
        """Full risk assessment combining all checks."""
        import asyncio
        contract_task = self.check_contract_risk(contract, chain) if contract else asyncio.coroutine(lambda: {"risk_score": 50})()
        rug_task = self.check_rug_indicators(symbol, coingecko_id)
        tokenomics_task = self.analyze_tokenomics(coingecko_id) if coingecko_id else asyncio.coroutine(lambda: {"score": 50})()

        contract_risk, rug_indicators, tokenomics = await asyncio.gather(
            contract_task, rug_task, tokenomics_task, return_exceptions=True
        )

        c_score = contract_risk.get("risk_score", 50) if isinstance(contract_risk, dict) else 50
        r_score = rug_indicators.get("risk_score", 50) if isinstance(rug_indicators, dict) else 50
        t_score = tokenomics.get("score", 50) if isinstance(tokenomics, dict) else 50

        overall = self.compute_score({
            "contract_risk_score": c_score,
            "rug_risk_score": r_score,
            "tokenomics_score": t_score,
        })

        # Risk category
        if overall <= 25:
            category = "LOW"
        elif overall <= 50:
            category = "MEDIUM"
        elif overall <= 75:
            category = "HIGH"
        else:
            category = "EXTREME"

        return {
            "symbol": symbol,
            "risk_score": overall,
            "risk_category": category,
            "contract_risk": contract_risk if isinstance(contract_risk, dict) else {},
            "rug_indicators": rug_indicators if isinstance(rug_indicators, dict) else {},
            "tokenomics": tokenomics if isinstance(tokenomics, dict) else {},
        }
