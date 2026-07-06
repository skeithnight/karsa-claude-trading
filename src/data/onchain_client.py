"""Karsa Trading System — Multi-Chain On-Chain Data Client

Block explorer APIs for on-chain data: holder counts, transfers, contract info.
Supports Etherscan, BscScan, Solscan (optional API keys for higher limits).

Each explorer has its own base URL and response format — normalized internally.
"""

import asyncio
import time
from typing import Any

import aiohttp

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("onchain_client")

_CIRCUIT_BREAKER_TTL = 600
_MAX_FAILURES = 5
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]

_EXPLORER_URLS = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "base": "https://api.basescan.org/api",
    "polygon": "https://api.polygonscan.com/api",
}

_EXPLORER_KEYS = {
    "ethereum": settings.ETHERSCAN_API_KEY,
    "bsc": settings.BSCSCAN_API_KEY,
}


class OnchainClient:
    """Multi-chain on-chain data client via block explorer APIs."""

    def __init__(self, cache=None):
        self._cache = cache
        self._session: aiohttp.ClientSession | None = None
        self._failures: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}
        self._last_request: dict[str, float] = {}
        self._min_interval = 0.2

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"accept": "application/json"})
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _is_blocked(self, chain: str) -> bool:
        return time.time() < self._blocked_until.get(chain, 0)

    def _record_failure(self, chain: str):
        self._failures[chain] = self._failures.get(chain, 0) + 1
        if self._failures[chain] >= _MAX_FAILURES:
            self._blocked_until[chain] = time.time() + _CIRCUIT_BREAKER_TTL
            logger.warning("onchain_circuit_breaker_open", chain=chain)

    def _record_success(self, chain: str):
        self._failures[chain] = 0

    async def _throttle(self, chain: str):
        last = self._last_request.get(chain, 0)
        elapsed = time.time() - last
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request[chain] = time.time()

    def _cache_key(self, chain: str, endpoint: str) -> str:
        return f"karsa:aode:oc:{chain}:{endpoint}"

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

    async def _explorer_request(self, chain: str, params: dict) -> dict | None:
        """Request to EVM-compatible block explorer."""
        base_url = _EXPLORER_URLS.get(chain)
        if not base_url:
            logger.warning("unsupported_chain", chain=chain)
            return None
        if self._is_blocked(chain):
            return None

        api_key = _EXPLORER_KEYS.get(chain, "")
        if api_key:
            params["apikey"] = api_key

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                await self._throttle(chain)
                session = await self._get_session()
                async with session.get(base_url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "1" or data.get("result"):
                            self._record_success(chain)
                            return data
                        last_error = data.get("message", "unknown_error")
                        break
                    last_error = f"HTTP {resp.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = str(e)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])

        self._record_failure(chain)
        logger.error("onchain_request_failed", chain=chain, error=last_error)
        return None

    async def get_token_transfers(self, contract: str, chain: str = "ethereum", days: int = 7) -> list[dict]:
        """Get recent ERC20 token transfers for a contract."""
        cache_key = self._cache_key(chain, f"transfers:{contract}:{days}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._explorer_request(chain, {
            "module": "account",
            "action": "tokentx",
            "contractaddress": contract,
            "page": 1,
            "offset": 100,
            "sort": "desc",
        })

        if not data:
            return []

        transfers = []
        for tx in data.get("result", [])[:100]:
            if isinstance(tx, dict):
                transfers.append({
                    "hash": tx.get("hash"),
                    "from": tx.get("from"),
                    "to": tx.get("to"),
                    "value": tx.get("value"),
                    "token_symbol": tx.get("tokenSymbol"),
                    "block_number": tx.get("blockNumber"),
                    "timestamp": tx.get("timeStamp"),
                })

        await self._set_cache(cache_key, transfers, 120)
        return transfers

    async def get_eth_balance(self, address: str, chain: str = "ethereum") -> float | None:
        """Get ETH/BNB balance for an address."""
        data = await self._explorer_request(chain, {
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
        })
        if not data:
            return None
        try:
            return int(data.get("result", 0)) / 1e18
        except (ValueError, TypeError):
            return None

    async def get_contract_info(self, contract: str, chain: str = "ethereum") -> dict | None:
        """Get contract source code and ABI verification status."""
        cache_key = self._cache_key(chain, f"contract:{contract}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._explorer_request(chain, {
            "module": "contract",
            "action": "getsourcecode",
            "address": contract,
        })
        if not data or not data.get("result"):
            return None

        result_raw = data["result"]
        info = result_raw[0] if isinstance(result_raw, list) and result_raw else result_raw
        if not isinstance(info, dict):
            return None

        result = {
            "contract_name": info.get("ContractName"),
            "compiler": info.get("CompilerVersion"),
            "verified": bool(info.get("SourceCode")),
            "proxy": info.get("Proxy") == "1",
            "implementation": info.get("Implementation"),
            "is_contract": bool(info.get("ABI") and info.get("ABI") != "Contract source code not verified"),
            "optimization": info.get("OptimizationUsed"),
        }

        await self._set_cache(cache_key, result, 86400)
        return result

    async def get_solana_token_holders(self, mint: str) -> list[dict]:
        """Get top holders for a Solana token via public RPC."""
        cache_key = self._cache_key("solana", f"holders:{mint}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        try:
            session = await self._get_session()
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [mint],
            }
            async with session.post("https://api.mainnet-beta.solana.com", json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                accounts = data.get("result", {}).get("value", [])

                holders = []
                for acc in accounts[:20]:
                    holders.append({
                        "address": acc.get("address"),
                        "amount": acc.get("amount"),
                        "decimals": acc.get("decimals"),
                    })

                await self._set_cache(cache_key, holders, 300)
                return holders
        except Exception as e:
            logger.error("solana_holders_failed", mint=mint, error=str(e))
            return []
