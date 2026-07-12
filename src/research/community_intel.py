"""Community Intelligence — Social metrics and community health.

Scoring (0-100). Social APIs are fragile — degrades gracefully.
"""

from src.utils.logging import get_logger

logger = get_logger("community_intel")


class CommunityIntelligence:
    """Community/social metrics collection and scoring."""

    def __init__(self, cache=None):
        self._cache = cache

    async def _ensure_clients(self):
        pass

    async def close(self):
        """Close all underlying HTTP clients to prevent connection leaks."""
        pass

    async def get_social_metrics(self, coingecko_id: str) -> dict:
        """Get social metrics — CoinGecko removed, returns empty."""
        return {}

    def compute_score(self, metrics: dict) -> float:
        """Score 0-100 based on community health."""
        import math
        score = 0.0

        # Twitter followers (0-25): 100K+ = full score
        twitter = metrics.get("twitter_followers") or 0
        if twitter > 0:
            score += min(25, (math.log10(max(twitter, 1)) / 5) * 25)

        # Reddit subscribers (0-20): 50K+ = full score
        reddit = metrics.get("reddit_subscribers") or 0
        if reddit > 0:
            score += min(20, (math.log10(max(reddit, 1)) / 4.7) * 20)

        # Telegram members (0-20): 20K+ = full score
        telegram = metrics.get("telegram_members") or 0
        if telegram > 0:
            score += min(20, (math.log10(max(telegram, 1)) / 4.3) * 20)

        # Reddit engagement (0-15): avg posts > 10/48h = full
        posts = metrics.get("reddit_avg_posts_48h") or 0
        score += min(15, (posts / 10) * 15)

        # Sentiment (0-10): >60% positive = full
        up_pct = metrics.get("sentiment_up_pct") or 0
        if up_pct > 50:
            score += min(10, ((up_pct - 50) / 30) * 10)

        # GitHub stars bonus (0-10): community interest indicator
        stars = metrics.get("github_stars") or 0
        if stars > 0:
            score += min(10, (math.log10(max(stars, 1)) / 3) * 10)

        return round(min(100, score), 2)

    async def analyze(self, symbol: str, coingecko_id: str | None = None) -> dict:
        """Full community analysis."""
        if not coingecko_id:
            return {"symbol": symbol, "score": 0, "error": "no_coingecko_id"}

        metrics = await self.get_social_metrics(coingecko_id)
        if not metrics:
            return {"symbol": symbol, "score": 0, "error": "no_data"}

        score = self.compute_score(metrics)
        return {"symbol": symbol, "score": score, "metrics": metrics}

    async def snapshot(self, symbol: str, coingecko_id: str | None = None) -> dict:
        return await self.analyze(symbol, coingecko_id)

    async def persist(self, symbol: str, metrics: dict):
        """Save to community_snapshots table."""
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            for platform, count in [
                ("twitter", metrics.get("twitter_followers")),
                ("reddit", metrics.get("reddit_subscribers")),
                ("telegram", metrics.get("telegram_members")),
            ]:
                if count:
                    await session.execute(
                        text("""INSERT INTO community_snapshots
                            (symbol, platform, member_count, sentiment_score)
                            VALUES (:symbol, :platform, :count, :sentiment)"""),
                        {
                            "symbol": symbol, "platform": platform,
                            "count": count, "sentiment": metrics.get("sentiment_up_pct"),
                        },
                    )
            await session.commit()
