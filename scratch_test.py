import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.advisory.universe_scorer import rank_candidates

candidates = [
    {"symbol": "BTCUSDT", "volume_24h_usd": 50_000_000_000, "price_change_pct": 2.0, "turnover_ratio": 1.5},
    {"symbol": "ETHUSDT", "volume_24h_usd": 20_000_000_000, "price_change_pct": 3.0, "turnover_ratio": 1.2},
    # 5 AI coins with massive volume
    {"symbol": "FETUSDT", "volume_24h_usd": 500_000_000, "price_change_pct": 10.0, "turnover_ratio": 2.5},
    {"symbol": "WLDUSDT", "volume_24h_usd": 400_000_000, "price_change_pct": 9.0, "turnover_ratio": 2.0},
    {"symbol": "RNDRUSDT", "volume_24h_usd": 350_000_000, "price_change_pct": 8.0, "turnover_ratio": 1.8},
    {"symbol": "AGIXUSDT", "volume_24h_usd": 300_000_000, "price_change_pct": 7.0, "turnover_ratio": 1.5},
    {"symbol": "TAOUSDT", "volume_24h_usd": 250_000_000, "price_change_pct": 6.0, "turnover_ratio": 1.2},
    # 1 DeFi coin with slightly lower stats
    {"symbol": "UNIUSDT", "volume_24h_usd": 200_000_000, "price_change_pct": 5.0, "turnover_ratio": 1.1},
]

sector_mapping = {
    "BTCUSDT": "Layer1",
    "ETHUSDT": "Layer1",
    "FETUSDT": "AI",
    "WLDUSDT": "AI",
    "RNDRUSDT": "AI",
    "AGIXUSDT": "AI",
    "TAOUSDT": "AI",
    "UNIUSDT": "DeFi",
}

ranked = rank_candidates(
    candidates,
    top_n=5,
    min_score=20.0,
    always_include={"BTCUSDT", "ETHUSDT"},
    sector_mapping=sector_mapping,
    max_per_sector=2,
    sector_penalty=20.0
)

print("Top 5 Selected:")
for r in ranked:
    print(f"{r['symbol']} | Score: {r['score']:.2f} (Base: {r['base_score']:.2f}) | Sector: {r['sector']}")
