"""Karsa Trading System — GitHub API Client

Developer activity data: repos, commits, contributors, releases.
Optional GITHUB_TOKEN for 5000 req/hr (vs 60 unauthenticated).

Base URL: https://api.github.com
"""

import asyncio
import time
from typing import Any

import aiohttp

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("github_client")

_BASE_URL = "https://api.github.com"
_CIRCUIT_BREAKER_TTL = 600
_MAX_FAILURES = 5
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]

_CACHE_TTL_STATS = 3600
_CACHE_TTL_COMMITS = 900
_CACHE_TTL_CONTRIBUTORS = 3600
_CACHE_TTL_RELEASES = 3600

class GitHubClient:
    """GitHub REST API client for developer intelligence."""

    def __init__(self, cache=None):
        self._cache = cache
        self._session: aiohttp.ClientSession | None = None
        self._failures = 0
        self._blocked_until = 0.0
        self._last_request = 0.0
        self._min_interval = 1.0 if not settings.GITHUB_TOKEN else 0.1

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"accept": "application/vnd.github.v3+json"}
            if settings.GITHUB_TOKEN:
                headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
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
            logger.warning("github_circuit_breaker_open")

    def _record_success(self):
        self._failures = 0

    async def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _cache_key(self, endpoint: str) -> str:
        return f"karsa:aode:gh:{endpoint}"

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

    async def _request(self, path: str, params: dict | None = None) -> dict | list | None:
        if self._is_blocked():
            return None

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                await self._throttle()
                session = await self._get_session()
                async with session.get(f"{_BASE_URL}{path}", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        self._record_success()
                        return await resp.json()
                    if resp.status == 403:
                        reset = int(resp.headers.get("X-RateLimit-Reset", 0))
                        wait = max(0, reset - int(time.time())) + 1
                        if wait < 300:
                            logger.warning("github_rate_limited", wait=wait)
                            await asyncio.sleep(wait)
                            continue
                    last_error = f"HTTP {resp.status}"
                    if resp.status in (401, 404):
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = str(e)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])

        self._record_failure()
        logger.error("github_request_failed", path=path, error=last_error)
        return None

    async def get_repo_stats(self, owner: str, repo: str) -> dict | None:
        """Get repo stats: stars, forks, issues, language, created_at."""
        cache_key = self._cache_key(f"repo:{owner}/{repo}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"/repos/{owner}/{repo}")
        if not data:
            return None

        result = {
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "open_issues": data.get("open_issues_count"),
            "language": data.get("language"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "pushed_at": data.get("pushed_at"),
            "size_kb": data.get("size"),
            "archived": data.get("archived"),
            "topics": data.get("topics", []),
            "license": (data.get("license") or {}).get("spdx_id"),
            "default_branch": data.get("default_branch"),
            "has_wiki": data.get("has_wiki"),
            "homepage": data.get("homepage"),
        }

        await self._set_cache(cache_key, result, _CACHE_TTL_STATS)
        return result

    async def get_recent_commits(self, owner: str, repo: str, days: int = 30) -> list[dict]:
        """Get recent commits (last N days)."""
        cache_key = self._cache_key(f"commits:{owner}/{repo}:{days}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        data = await self._request(f"/repos/{owner}/{repo}/commits", {"since": since, "per_page": 100})
        if not data:
            return []

        commits = []
        for c in data[:100]:
            commit_info = c.get("commit", {})
            commits.append({
                "sha": c.get("sha", "")[:7],
                "message": (commit_info.get("message") or "")[:200],
                "author": (commit_info.get("author") or {}).get("name"),
                "date": (commit_info.get("author") or {}).get("date"),
            })

        await self._set_cache(cache_key, commits, _CACHE_TTL_COMMITS)
        return commits

    async def get_contributors(self, owner: str, repo: str, n: int = 30) -> list[dict]:
        """Top contributors by commit count."""
        cache_key = self._cache_key(f"contributors:{owner}/{repo}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"/repos/{owner}/{repo}/contributors", {"per_page": n})
        if not data:
            return []

        contributors = [
            {"login": c.get("login"), "contributions": c.get("contributions")}
            for c in data[:n]
        ]

        await self._set_cache(cache_key, contributors, _CACHE_TTL_CONTRIBUTORS)
        return contributors

    async def get_releases(self, owner: str, repo: str, n: int = 10) -> list[dict]:
        """Recent releases."""
        cache_key = self._cache_key(f"releases:{owner}/{repo}")
        cached = await self._get_cache(cache_key)
        if cached:
            return cached

        data = await self._request(f"/repos/{owner}/{repo}/releases", {"per_page": n})
        if not data:
            return []

        releases = [
            {
                "tag": r.get("tag_name"),
                "name": r.get("name"),
                "published_at": r.get("published_at"),
                "prerelease": r.get("prerelease"),
            }
            for r in data[:n]
        ]

        await self._set_cache(cache_key, releases, _CACHE_TTL_RELEASES)
        return releases

    async def get_activity_score(self, owner: str, repo: str) -> dict:
        """Compute developer activity metrics for scoring."""
        stats = await self.get_repo_stats(owner, repo)
        if not stats:
            return {"score": 0, "error": "repo_not_found"}

        commits = await self.get_recent_commits(owner, repo, 30)
        contributors = await self.get_contributors(owner, repo, 10)
        releases = await self.get_releases(owner, repo, 5)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        last_push = None
        if stats.get("pushed_at"):
            try:
                last_push = datetime.fromisoformat(stats["pushed_at"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        days_since_push = (now - last_push).days if last_push else 999

        return {
            "stars": stats.get("stars", 0),
            "forks": stats.get("forks", 0),
            "commits_30d": len(commits),
            "contributors_active": len(contributors),
            "releases_count": len(releases),
            "days_since_push": days_since_push,
            "open_issues": stats.get("open_issues", 0),
            "archived": stats.get("archived", False),
        }
