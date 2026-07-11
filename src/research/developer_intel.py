"""Developer Intelligence — GitHub activity analysis.

Deterministic scoring (0-100). No LLM calls.
Uses: GitHubClient, CoinGeckoClient (for repo URLs).
"""

from src.utils.logging import get_logger

logger = get_logger("developer_intel")


class DeveloperIntelligence:
    """Developer activity collection and scoring."""

    def __init__(self, cache=None, github=None, coingecko=None):
        self._cache = cache
        self._gh = github
        self._cg = coingecko

    async def _ensure_clients(self):
        from src.data.github_client import GitHubClient
        from src.data.coingecko_client import CoinGeckoClient
        if not self._gh:
            self._gh = GitHubClient(cache=self._cache)
        if not self._cg:
            self._cg = CoinGeckoClient(cache=self._cache)

    async def close(self):
        """Close all underlying HTTP clients to prevent connection leaks."""
        for client in (self._gh, self._cg):
            if client and hasattr(client, 'close'):
                await client.close()

    async def get_repo_stats(self, owner: str, repo: str) -> dict | None:
        await self._ensure_clients()
        return await self._gh.get_repo_stats(owner, repo)

    async def get_commit_activity(self, owner: str, repo: str, days: int = 30) -> dict:
        await self._ensure_clients()
        commits = await self._gh.get_recent_commits(owner, repo, days)
        contributors = await self._gh.get_contributors(owner, repo, 20)
        releases = await self._gh.get_releases(owner, repo, 5)
        return {
            "commits": len(commits),
            "contributors": len(contributors),
            "releases": len(releases),
            "last_commit_date": commits[0].get("date") if commits else None,
        }

    async def get_doc_quality(self, owner: str, repo: str) -> float:
        """Score documentation quality 0-100 based on repo metadata."""
        await self._ensure_clients()
        stats = await self._gh.get_repo_stats(owner, repo)
        if not stats:
            return 0.0

        score = 0.0
        # Has README (repo exists = has README on GitHub)
        score += 30
        # Has wiki
        if stats.get("has_wiki"):
            score += 10
        # Has homepage/docs URL
        if stats.get("homepage"):
            score += 15
        # Has topics (indicates good categorization)
        topics = stats.get("topics") or []
        if len(topics) >= 3:
            score += 15
        elif len(topics) >= 1:
            score += 5
        # Has license
        if stats.get("license"):
            score += 10
        # Not archived
        if not stats.get("archived"):
            score += 10
        # Recent activity
        if stats.get("pushed_at"):
            from datetime import datetime, timezone
            try:
                pushed = datetime.fromisoformat(stats["pushed_at"].replace("Z", "+00:00"))
                days_ago = (datetime.now(timezone.utc) - pushed).days
                if days_ago < 7:
                    score += 10
                elif days_ago < 30:
                    score += 5
            except (ValueError, TypeError):
                pass
        return min(100, score)

    def compute_score(self, metrics: dict) -> float:
        """Score 0-100 based on developer activity."""
        score = 0.0

        # Commit frequency (0-30): 30+ commits/30d = full score
        commits = metrics.get("commits_30d") or 0
        score += min(30, (commits / 30) * 30)

        # Contributors (0-20): 10+ active = full score
        contributors = metrics.get("contributors_active") or 0
        score += min(20, (contributors / 10) * 20)

        # Releases (0-15): any release in last 90d = 15
        releases = metrics.get("releases_count") or 0
        score += min(15, releases * 5)

        # Stars (0-15): 1000+ = full score (log scale)
        import math
        stars = metrics.get("stars") or 0
        if stars > 0:
            score += min(15, (math.log10(max(stars, 1)) / 3) * 15)

        # Recent push (0-10): pushed within 7 days = 10
        days_since = metrics.get("days_since_push", 999)
        if days_since < 3:
            score += 10
        elif days_since < 7:
            score += 7
        elif days_since < 30:
            score += 3

        # Doc quality (0-10)
        score += min(10, (metrics.get("doc_score") or 0) / 10)

        return round(min(100, score), 2)

    async def analyze(self, symbol: str, coingecko_id: str | None = None) -> dict:
        """Full developer analysis for a token."""
        await self._ensure_clients()

        # Find GitHub repos from CoinGecko
        repos = []
        if coingecko_id:
            detail = await self._cg.get_coin_detail(coingecko_id)
            if detail:
                github_urls = detail.get("links", {}).get("github") or []
                for url in github_urls[:3]:
                    parts = url.rstrip("/").split("/")
                    if len(parts) >= 2:
                        repos.append((parts[-2], parts[-1]))

        if not repos:
            return {"symbol": symbol, "score": 0, "error": "no_github_found"}

        # Analyze first repo (primary)
        owner, repo = repos[0]
        activity = await self._gh.get_activity_score(owner, repo)
        doc_score = await self.get_doc_quality(owner, repo)

        metrics = {**activity, "doc_score": doc_score}
        score = self.compute_score(metrics)

        return {
            "symbol": symbol,
            "repo": f"{owner}/{repo}",
            "score": score,
            "metrics": metrics,
        }

    async def snapshot(self, symbol: str, coingecko_id: str | None = None) -> dict:
        """Analyze and return scored result."""
        result = await self.analyze(symbol, coingecko_id)
        return result

    async def persist(self, symbol: str, repo_url: str, metrics: dict):
        """Save to developer_snapshots table."""
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(
                text("""INSERT INTO developer_snapshots
                    (symbol, repo_url, stars, forks, open_issues, commits_30d,
                     contributors_30d, pull_requests_30d, documentation_score)
                    VALUES (:symbol, :repo, :stars, :forks, :issues, :commits,
                            :contributors, :prs, :doc_score)"""),
                {
                    "symbol": symbol, "repo": repo_url,
                    "stars": metrics.get("stars"),
                    "forks": metrics.get("forks"),
                    "issues": metrics.get("open_issues"),
                    "commits": metrics.get("commits_30d"),
                    "contributors": metrics.get("contributors_active"),
                    "prs": 0,  # not tracked separately yet
                    "doc_score": metrics.get("doc_score"),
                },
            )
            await session.commit()
