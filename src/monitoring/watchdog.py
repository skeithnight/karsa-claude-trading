"""Karsa Trading System — Advanced Service Watchdog v2

Predictive health monitor with graduated recovery and prevention.

Architecture:
  ┌─────────────────────────────────────────────┐
  │              ServiceWatchdog                 │
  │  ┌─────────┐  ┌─────────┐  ┌─────────────┐ │
  │  │ Health   │  │Predictive│  │ Diagnostic  │ │
  │  │ Checker  │  │ Analyzer │  │ Capture     │ │
  │  └────┬────┘  └────┬────┘  └──────┬──────┘ │
  │       │            │              │         │
  │  ┌────▼────────────▼──────────────▼──────┐ │
  │  │         Recovery Engine               │ │
  │  │  Level 1: Self-heal (verify after)    │ │
  │  │  Level 2: Soft restart (signal)       │ │
  │  │  Level 3: Hard restart (exit)         │ │
  │  └──────────────────────────────────────┘ │
  └─────────────────────────────────────────────┘

Prevention features:
  - Health score (0-100) with trend analysis
  - Predictive alerts when score trends downward
  - Event loop stall detection via sentinel task
  - Memory growth tracking (RSS trend)
  - Dependency-aware failure counting (Redis down = 1, not N)
  - Alert throttling (min 5min between same alerts)
  - Recovery verification (re-check after heal)
  - Rate-limited escalation (min 2min between levels)
  - Diagnostic snapshot before hard restart
  - Circuit breaker on individual checks

Flow:
  Service starts → watchdog.start() →
  Sentinel task writes heartbeat every 5s →
  Every 30s: collect health signals → compute score →
    Score > 70: healthy (log only)
    Score 40-70: degraded (Level 1 heal, verify)
    Score < 40: critical (Level 2/3 based on trend)
"""

import asyncio
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from src.utils.logging import get_logger

logger = get_logger("watchdog")

# ── Thresholds ──────────────────────────────────────────────
HEARTBEAT_STALE_SEC = 180       # 3 min without heartbeat = stale
CHECK_INTERVAL_SEC = 30         # Main check interval
SENTINEL_INTERVAL_SEC = 5       # Self-heartbeat interval
ALERT_THROTTLE_SEC = 300        # 5 min between same alerts
LEVEL_ESCALATION_MIN_SEC = 120  # 2 min minimum between level transitions
SCORE_HISTORY_SIZE = 20         # Last 20 checks for trend analysis
MEMORY_CHECK_INTERVAL = 60      # Check memory every 60s

# Health score weights
SCORE_WEIGHTS = {
    "redis": 25,        # Redis is critical — 25% of score
    "heartbeats": 20,   # Subsystem liveness — 20%
    "db_pool": 15,      # DB health — 15%
    "event_loop": 10,   # Event loop responsiveness — 10%
    "warp_proxy": 15,   # WARP SOCKS5 proxy health — 15%
    "memory": 5,        # Memory health — 5%
    "pool_leak": 10,    # DB pool overflow leak — 10%
}

# Score thresholds
SCORE_HEALTHY = 70
SCORE_DEGRADED = 40
# Below SCORE_DEGRADED = critical

# Memory thresholds
MEMORY_GROWTH_WARN_MB = 100  # Warn if RSS grew > 100MB since last check
MEMORY_ABSOLUTE_MAX_MB = 2048  # Hard limit 2GB

# Redis keys
REDIS_PREFIX = "karsa:watchdog"
REDIS_RESTART_SIGNAL = "karsa:watchdog:restart_signal"
REDIS_RESTART_REASON = "karsa:watchdog:restart_reason"
REDIS_DIAGNOSTIC = "karsa:watchdog:diagnostic"
REDIS_HEALTH_SCORE = "karsa:watchdog:health_score"
REDIS_SENTINEL = "karsa:watchdog:sentinel"


@dataclass
class HealthSignal:
    """A single health signal from a subsystem check."""
    name: str
    healthy: bool
    score: float  # 0-100
    detail: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class DiagnosticSnapshot:
    """State captured before hard restart for post-mortem."""
    timestamp: float
    service: str
    health_score: float
    score_history: list[float]
    issues: list[str]
    heartbeats: dict[str, float]
    memory_mb: float
    event_loop_lag_sec: float
    failure_count: int
    level: int

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "service": self.service,
            "health_score": round(self.health_score, 1),
            "score_trend": [round(s, 1) for s in self.score_history[-10:]],
            "issues": self.issues,
            "heartbeat_ages": {
                k: round(time.time() - v, 1)
                for k, v in self.heartbeats.items()
            },
            "memory_mb": round(self.memory_mb, 1),
            "event_loop_lag_sec": round(self.event_loop_lag_sec, 3),
            "failure_count": self.failure_count,
            "level": self.level,
        })


class ServiceWatchdog:
    """Advanced in-process watchdog with predictive health and prevention."""

    def __init__(self, redis_client, service_name: str, chat_id: int = 0):
        self._redis = redis_client
        self._service = service_name
        self._chat_id = chat_id
        self._telegram_bot = None

        # State
        self._heartbeats: dict[str, float] = {}
        self._failure_count = 0
        self._running = False
        self._current_level = 0  # 0=healthy, 1=heal, 2=soft, 3=hard

        # Health tracking
        self._score_history: deque[float] = deque(maxlen=SCORE_HISTORY_SIZE)
        self._last_score = 100.0

        # Event loop sentinel
        self._sentinel_last_write = 0.0
        self._sentinel_lag = 0.0

        # Memory tracking
        self._last_memory_mb = 0.0
        self._memory_baseline_mb = 0.0

        # Alert throttling
        self._last_alerts: dict[str, float] = {}  # alert_key → timestamp

        # Escalation rate limiting
        self._last_level_change = 0.0

        # Circuit breaker per check
        self._check_failures: dict[str, int] = {}
        self._check_disabled_until: dict[str, float] = {}

        # Startup grace — skip pool leak check on first N cycles
        self._check_cycle = 0

        # Custom handlers
        self._level1_handlers: list[Callable[[], Awaitable[None]]] = []
        self._level2_handlers: list[Callable[[], Awaitable[None]]] = []

    def set_telegram_bot(self, bot):
        self._telegram_bot = bot

    def register_level1_handler(self, handler: Callable[[], Awaitable[None]]):
        self._level1_handlers.append(handler)

    def register_level2_handler(self, handler: Callable[[], Awaitable[None]]):
        self._level2_handlers.append(handler)

    async def start(self):
        """Start watchdog and sentinel tasks."""
        if self._running:
            return
        self._running = True

        # Initialize memory baseline
        self._memory_baseline_mb = self._get_memory_mb()
        self._last_memory_mb = self._memory_baseline_mb

        asyncio.create_task(self._loop())
        asyncio.create_task(self._sentinel_loop())
        logger.info("watchdog_started", service=self._service,
                     memory_baseline_mb=round(self._memory_baseline_mb, 1))

    async def stop(self):
        self._running = False
        logger.info("watchdog_stopped", service=self._service)

    async def register_heartbeat(self, subsystem: str):
        """Subsystems call this periodically to signal liveness."""
        now = time.time()
        self._heartbeats[subsystem] = now
        try:
            await self._redis.set(
                f"{REDIS_PREFIX}:{self._service}:{subsystem}",
                str(now), ex=300
            )
        except Exception:
            pass

    # ── Sentinel (event loop health) ────────────────────────

    async def _sentinel_loop(self):
        """Sentinel task — writes timestamp every 5s.

        If the event loop is starved, this task can't run, and the main
        watchdog loop detects the lag via _sentinel_last_write staleness.
        """
        while self._running:
            try:
                now = time.time()
                if self._sentinel_last_write > 0:
                    self._sentinel_lag = now - self._sentinel_last_write - SENTINEL_INTERVAL_SEC
                self._sentinel_last_write = now
                await self._redis.set(REDIS_SENTINEL, str(now), ex=30)
            except Exception:
                pass
            await asyncio.sleep(SENTINEL_INTERVAL_SEC)

    # ── Main loop ───────────────────────────────────────────

    async def _loop(self):
        """Main watchdog loop — runs every 30s."""
        while self._running:
            try:
                signals = await self._collect_signals()
                score = self._compute_score(signals)
                self._score_history.append(score)
                self._last_score = score

                # Publish score to Redis for external monitoring
                try:
                    await self._redis.set(REDIS_HEALTH_SCORE, str(round(score, 1)), ex=120)
                except Exception:
                    pass

                # Publish Prometheus metrics
                try:
                    from src.metrics.crypto_metrics import (
                        WATCHDOG_HEALTH_SCORE, WATCHDOG_EVENT_LOOP_LAG,
                        WATCHDOG_MEMORY_MB, WATCHDOG_LEVEL, WATCHDOG_FAILURE_COUNT,
                        WATCHDOG_HEARTBEAT,
                    )
                    WATCHDOG_HEALTH_SCORE.labels(service=self._service).set(round(score, 1))
                    WATCHDOG_EVENT_LOOP_LAG.labels(service=self._service).set(round(self._sentinel_lag, 2))
                    WATCHDOG_MEMORY_MB.labels(service=self._service).set(round(self._last_memory_mb, 1))
                    WATCHDOG_LEVEL.labels(service=self._service).set(self._current_level)
                    WATCHDOG_FAILURE_COUNT.labels(service=self._service).set(self._failure_count)
                    for sub, ts in self._heartbeats.items():
                        WATCHDOG_HEARTBEAT.labels(service=self._service, subsystem=sub).set(ts)
                except Exception:
                    pass

                issues = [s.name for s in signals if not s.healthy]

                if score >= SCORE_HEALTHY:
                    # Healthy — reset failure count if we were degraded
                    if self._failure_count > 0:
                        await self._on_recovery()
                    self._failure_count = 0
                    self._current_level = 0
                elif score >= SCORE_DEGRADED:
                    # Degraded — Level 1 heal
                    await self._escalate(1, issues, score)
                else:
                    # Critical — check trend for Level 2 vs 3
                    trend = self._score_trend()
                    if trend < -5 and self._current_level >= 2:
                        # Score dropping fast, already tried Level 2 → Level 3
                        await self._escalate(3, issues, score)
                    else:
                        await self._escalate(2, issues, score)

                # Predictive alert: warn if score trending down
                await self._check_predictive_alert(score)

            except Exception as e:
                logger.error("watchdog_loop_error", error=str(e))
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    # ── Signal collection ───────────────────────────────────

    async def _collect_signals(self) -> list[HealthSignal]:
        """Collect health signals from all subsystems."""
        signals = []

        # 1. Redis connectivity (weight: 25)
        signals.append(await self._check_redis())

        # 2. Subsystem heartbeats (weight: 20)
        signals.append(self._check_heartbeats())

        # 3. DB pool health (weight: 15)
        signals.append(await self._check_db_pool())

        # 4. Event loop responsiveness (weight: 10)
        signals.append(self._check_event_loop())

        # 5. WARP SOCKS5 proxy (weight: 15)
        signals.append(await self._check_warp_proxy())

        # 6. Memory health (weight: 5)
        signals.append(self._check_memory())

        # 7. DB pool leak detection (weight: 10)
        signals.append(await self._check_pool_leak())

        return signals

    async def _check_redis(self) -> HealthSignal:
        """Check Redis connectivity."""
        if self._is_circuit_broken("redis"):
            return HealthSignal("redis", False, 0, "circuit broken")
        try:
            start = time.monotonic()
            await self._redis.ping()
            latency = time.monotonic() - start
            score = max(0, 100 - latency * 1000)  # 1000ms = 0 score
            self._check_failures["redis"] = 0
            return HealthSignal("redis", True, score, f"latency={latency*1000:.0f}ms")
        except Exception as e:
            self._record_check_failure("redis")
            return HealthSignal("redis", False, 0, str(e)[:80])

    def _check_heartbeats(self) -> HealthSignal:
        """Check subsystem heartbeats."""
        now = time.time()
        stale_count = 0
        total = len(self._heartbeats)
        details = []

        for subsystem, last_beat in self._heartbeats.items():
            age = now - last_beat
            if age > HEARTBEAT_STALE_SEC:
                stale_count += 1
                details.append(f"{subsystem}:{age:.0f}s")

        if total == 0:
            return HealthSignal("heartbeats", True, 100, "no subsystems registered")

        healthy_ratio = (total - stale_count) / total
        score = healthy_ratio * 100
        healthy = stale_count == 0

        return HealthSignal(
            "heartbeats", healthy, score,
            f"{total - stale_count}/{total} alive" + (f" stale: {','.join(details)}" if details else "")
        )

    async def _check_db_pool(self) -> HealthSignal:
        """Check DB connection pool health."""
        if self._is_circuit_broken("db_pool"):
            return HealthSignal("db_pool", False, 0, "circuit broken")
        # Startup grace: first 2 cycles may see stale overflow from previous run
        if self._check_cycle <= 2:
            return HealthSignal("db_pool", True, 100, "startup_grace")
        try:
            from src.models.database import get_engine
            engine = get_engine()
            pool = engine.pool
            overflow = pool.overflow()
            checked_out = pool.checkedout()

            if overflow < 0:
                self._record_check_failure("db_pool")
                return HealthSignal("db_pool", False, 0, f"leak: overflow={overflow}")

            # Score based on pool utilization
            # checked_out / (size + overflow) — higher = worse
            pool_size = pool.size()
            max_overflow = pool._max_overflow if hasattr(pool, '_max_overflow') else 10
            utilization = checked_out / max(1, pool_size + max_overflow)
            score = max(0, 100 - utilization * 100)

            self._check_failures["db_pool"] = 0
            return HealthSignal(
                "db_pool", True, score,
                f"checked_out={checked_out}, overflow={overflow}"
            )
        except Exception as e:
            self._record_check_failure("db_pool")
            return HealthSignal("db_pool", False, 0, str(e)[:80])

    def _check_event_loop(self) -> HealthSignal:
        """Check event loop responsiveness via sentinel lag."""
        lag = self._sentinel_lag

        if lag > 30:
            # Sentinel hasn't written in 30s+ — loop is severely starved
            return HealthSignal("event_loop", False, 0, f"lag={lag:.1f}s (SEVERE)")
        elif lag > 10:
            score = max(0, 50 - (lag - 10) * 5)
            return HealthSignal("event_loop", True, score, f"lag={lag:.1f}s (degraded)")
        elif lag > 2:
            score = max(50, 100 - lag * 10)
            return HealthSignal("event_loop", True, score, f"lag={lag:.1f}s")
        else:
            return HealthSignal("event_loop", True, 100, f"lag={lag:.1f}s")

    def _check_memory(self) -> HealthSignal:
        """Check process memory health."""
        current_mb = self._get_memory_mb()
        if current_mb <= 0:
            return HealthSignal("memory", True, 100, "unavailable")

        growth_mb = current_mb - self._last_memory_mb
        self._last_memory_mb = current_mb

        # Absolute limit
        if current_mb > MEMORY_ABSOLUTE_MAX_MB:
            return HealthSignal(
                "memory", False, 0,
                f"RSS={current_mb:.0f}MB > {MEMORY_ABSOLUTE_MAX_MB}MB limit"
            )

        # Growth warning
        if growth_mb > MEMORY_GROWTH_WARN_MB:
            score = max(0, 100 - (growth_mb - MEMORY_GROWTH_WARN_MB))
            return HealthSignal(
                "memory", True, score,
                f"RSS={current_mb:.0f}MB (+{growth_mb:.0f}MB growth)"
            )

        # Normal
        score = max(80, 100 - (current_mb / MEMORY_ABSOLUTE_MAX_MB) * 20)
        return HealthSignal("memory", True, score, f"RSS={current_mb:.0f}MB")

    def _get_memory_mb(self) -> float:
        """Get current process RSS in MB."""
        try:
            import resource
            # ru_maxrss is in KB on Linux, bytes on macOS
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss = usage.ru_maxrss
            # macOS returns bytes, Linux returns KB
            if rss > 1_000_000:  # Likely bytes (macOS)
                return rss / (1024 * 1024)
            else:  # Likely KB (Linux)
                return rss / 1024
        except Exception:
            return 0.0

    async def _check_warp_proxy(self) -> HealthSignal:
        """Check WARP SOCKS5 proxy connectivity to Bybit."""
        if self._is_circuit_broken("warp_proxy"):
            return HealthSignal("warp_proxy", False, 0, "circuit broken")
        try:
            import httpx
            start = time.monotonic()
            async with httpx.AsyncClient(
                proxies={"https://": "socks5h://warp:1080"},
                timeout=8.0,
            ) as client:
                resp = await client.get("https://api.bybit.com/v5/market/time")
                latency = time.monotonic() - start
                if resp.status_code == 200:
                    score = max(0, 100 - latency * 50)  # 2s = 0 score
                    self._check_failures["warp_proxy"] = 0
                    return HealthSignal("warp_proxy", True, score, f"latency={latency*1000:.0f}ms")
                else:
                    self._record_check_failure("warp_proxy")
                    return HealthSignal("warp_proxy", False, 0, f"HTTP {resp.status_code}")
        except Exception as e:
            self._record_check_failure("warp_proxy")
            return HealthSignal("warp_proxy", False, 0, str(e)[:80])

    async def _check_pool_leak(self) -> HealthSignal:
        """Check for DB pool overflow leak (negative overflow = double-returns)."""
        # Startup grace: skip first 2 cycles — fresh engine may report stale stats
        self._check_cycle += 1
        if self._check_cycle <= 2:
            return HealthSignal("pool_leak", True, 100, "startup_grace")
        try:
            from src.models.database import get_engine
            engine = get_engine()
            pool = engine.pool
            overflow = pool.overflow()
            if overflow < 0:
                # Pool leak detected — trigger auto-fix
                logger.warning("watchdog_pool_leak_detected", overflow=overflow)
                return HealthSignal("pool_leak", False, 0, f"overflow={overflow}")
            return HealthSignal("pool_leak", True, 100, f"overflow={overflow}")
        except Exception as e:
            return HealthSignal("pool_leak", True, 50, str(e)[:80])

    # ── Health scoring ──────────────────────────────────────

    def _compute_score(self, signals: list[HealthSignal]) -> float:
        """Compute weighted health score (0-100)."""
        total_weight = 0
        weighted_score = 0

        for signal in signals:
            weight = SCORE_WEIGHTS.get(signal.name, 10)
            weighted_score += signal.score * weight
            total_weight += weight

        if total_weight == 0:
            return 100.0

        return weighted_score / total_weight

    def _score_trend(self) -> float:
        """Compute score trend (positive = improving, negative = degrading).

        Uses linear regression slope over last N scores.
        """
        if len(self._score_history) < 3:
            return 0.0

        scores = list(self._score_history)
        n = len(scores)
        x_mean = (n - 1) / 2
        y_mean = sum(scores) / n

        numerator = sum((i - x_mean) * (s - y_mean) for i, s in enumerate(scores))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        return numerator / denominator  # slope per check

    # ── Escalation with rate limiting ───────────────────────

    async def _escalate(self, target_level: int, issues: list[str], score: float):
        """Escalate to target level with rate limiting."""
        now = time.time()

        # Rate limit: min 2 min between level changes
        if now - self._last_level_change < LEVEL_ESCALATION_MIN_SEC:
            logger.debug("watchdog_escalation_rate_limited",
                         target=target_level, current=self._current_level)
            return

        # Can only escalate, not de-escalate (de-escalation happens via recovery)
        if target_level <= self._current_level:
            return

        self._current_level = target_level
        self._failure_count += 1
        self._last_level_change = now

        # Record metrics
        try:
            from src.metrics.crypto_metrics import WATCHDOG_FAILURES, WATCHDOG_RECOVERIES
            for issue in issues:
                WATCHDOG_FAILURES.labels(service=self._service, issue=issue).inc()
            WATCHDOG_RECOVERIES.labels(service=self._service, level=f"level{target_level}").inc()
        except Exception:
            pass

        if target_level == 1:
            await self._level1_heal(issues, score)
        elif target_level == 2:
            await self._level2_soft_restart(issues, score)
        else:
            await self._level3_hard_restart(issues, score)

    async def _on_recovery(self):
        """Called when health score returns to healthy after being degraded."""
        logger.info("watchdog_recovered", service=self._service, score=round(self._last_score, 1))
        await self._throttled_alert(
            "recovery",
            f"✅ <b>Watchdog recovered</b> — {self._service} healthy (score={self._last_score:.0f})"
        )

    # ── Level 1: Self-heal with verification ────────────────

    async def _level1_heal(self, issues: list[str], score: float):
        """Level 1: Self-heal — reconnect, reset pool, VERIFY after."""
        logger.warning("watchdog_level1_heal", service=self._service,
                       issues=issues, score=round(score, 1))

        healed = []

        # Redis reconnect — create fresh connection, don't just ping
        if "redis" in issues:
            if await self._heal_redis():
                healed.append("redis")

        # DB pool reset
        if any("db_pool" in i for i in issues):
            if await self._heal_db_pool():
                healed.append("db_pool")

        # Run custom handlers
        for handler in self._level1_handlers:
            try:
                await handler()
            except Exception as e:
                logger.warning("watchdog_level1_handler_failed", error=str(e))

        # Verify: re-check critical signals after heal
        await asyncio.sleep(5)  # Give time for reconnect to settle
        verify_signals = await self._collect_signals()
        verify_score = self._compute_score(verify_signals)

        if verify_score >= SCORE_HEALTHY:
            await self._throttled_alert(
                "level1_success",
                f"⚠️ <b>Watchdog Level 1 Heal SUCCESS</b> ({self._service})\n"
                f"Issues: {', '.join(issues)}\n"
                f"Healed: {', '.join(healed)}\n"
                f"Score: {score:.0f} → {verify_score:.0f} ✅"
            )
        else:
            await self._throttled_alert(
                "level1_partial",
                f"⚠️ <b>Watchdog Level 1 Heal PARTIAL</b> ({self._service})\n"
                f"Issues: {', '.join(issues)}\n"
                f"Healed: {', '.join(healed) if healed else 'none'}\n"
                f"Score: {score:.0f} → {verify_score:.0f} (still degraded)"
            )

    async def _heal_redis(self) -> bool:
        """Attempt to heal Redis connection."""
        try:
            # Try ping first — might still work
            await self._redis.ping()
            logger.info("watchdog_redis_ping_ok")
            return True
        except Exception:
            pass

        # Ping failed — try creating a new connection
        try:
            import redis.asyncio as aioredis
            from src.config import settings
            new_conn = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await new_conn.ping()
            # Success — swap connections
            # Note: we can't easily swap the connection used by the rest of the app,
            # but at least we know Redis is reachable
            await new_conn.close()
            logger.info("watchdog_redis_new_connection_ok")
            return True
        except Exception as e:
            logger.warning("watchdog_redis_heal_failed", error=str(e))
            return False

    async def _heal_db_pool(self) -> bool:
        """Attempt to heal DB connection pool.

        Routes through pool_reset() — the single authorised dispose path —
        which holds the engine lock and resets both _engine and
        _session_factory so no coroutine can continue using the stale engine.
        """
        try:
            from src.models.database import pool_reset
            did_reset = await pool_reset("watchdog_heal")
            if did_reset:
                logger.warning("watchdog_db_pool_reset_ok")
            else:
                logger.debug("watchdog_db_pool_reset_cooldown_skipped")
            return True
        except Exception as e:
            logger.warning("watchdog_db_pool_heal_failed", error=str(e))
            return False

    # ── Level 2: Soft restart ───────────────────────────────

    async def _level2_soft_restart(self, issues: list[str], score: float):
        """Level 2: Signal main loop to restart tasks."""
        logger.warning("watchdog_level2_restart", service=self._service,
                       issues=issues, score=round(score, 1))

        # Set restart signal in Redis
        try:
            await self._redis.set(REDIS_RESTART_SIGNAL, "soft", ex=120)
            await self._redis.set(REDIS_RESTART_REASON, json.dumps({
                "issues": issues, "score": round(score, 1),
                "timestamp": time.time(),
            }), ex=600)
        except Exception:
            pass

        # Run custom handlers
        for handler in self._level2_handlers:
            try:
                await handler()
            except Exception as e:
                logger.warning("watchdog_level2_handler_failed", error=str(e))

        await self._throttled_alert(
            "level2",
            f"🔄 <b>Watchdog Level 2 Restart</b> ({self._service})\n"
            f"Issues: {', '.join(issues)}\n"
            f"Score: {score:.0f}\n"
            f"Restart signal sent."
        )

    # ── Level 3: Hard restart with diagnostics ──────────────

    async def _level3_hard_restart(self, issues: list[str], score: float):
        """Level 3: Capture diagnostics, then exit for Docker restart."""
        logger.critical("watchdog_level3_hard_restart", service=self._service,
                        issues=issues, score=round(score, 1))

        # Capture diagnostic snapshot
        snapshot = DiagnosticSnapshot(
            timestamp=time.time(),
            service=self._service,
            health_score=score,
            score_history=list(self._score_history),
            issues=issues,
            heartbeats=dict(self._heartbeats),
            memory_mb=self._last_memory_mb,
            event_loop_lag_sec=self._sentinel_lag,
            failure_count=self._failure_count,
            level=3,
        )

        # Save diagnostics to Redis (survives restart)
        try:
            await self._redis.set(REDIS_DIAGNOSTIC, snapshot.to_json(), ex=3600)
            await self._redis.set(REDIS_RESTART_REASON, json.dumps({
                "issues": issues, "score": round(score, 1),
                "diagnostic": snapshot.to_json(),
                "timestamp": time.time(),
            }), ex=3600)
        except Exception:
            pass

        await self._alert(
            f"🔴 <b>Watchdog Level 3 HARD RESTART</b> ({self._service})\n"
            f"Issues: {', '.join(issues)}\n"
            f"Score: {score:.0f} | Memory: {self._last_memory_mb:.0f}MB\n"
            f"Loop lag: {self._sentinel_lag:.1f}s\n"
            f"Process exiting — Docker will restart.\n\n"
            f"<code>{snapshot.to_json()[:500]}</code>"
        )

        # Record metric
        try:
            from src.metrics.crypto_metrics import WATCHDOG_RECOVERIES
            WATCHDOG_RECOVERIES.labels(service=self._service, level="level3").inc()
        except Exception:
            pass

        # Give time for metrics/alerts to flush
        await asyncio.sleep(3)

        # Exit — Docker restart: unless-stopped
        os._exit(1)

    # ── Predictive alerts ───────────────────────────────────

    async def _check_predictive_alert(self, current_score: float):
        """Warn if score is trending downward toward degraded territory."""
        if len(self._score_history) < 5:
            return

        trend = self._score_trend()
        # Predict score in 5 checks (2.5 min at 30s intervals)
        predicted = current_score + trend * 5

        if predicted < SCORE_DEGRADED and current_score > SCORE_DEGRADED:
            await self._throttled_alert(
                "predictive",
                f"🔮 <b>Predictive Alert</b> ({self._service})\n"
                f"Score: {current_score:.0f} (trend: {trend:+.1f}/check)\n"
                f"Predicted to hit degraded ({SCORE_DEGRADED}) in ~2.5min\n"
                f"Likely cause: {self._predict_cause()}"
            )

    def _predict_cause(self) -> str:
        """Identify the most likely cause of degradation."""
        if self._sentinel_lag > 5:
            return "event loop starvation (heavy async work)"
        if self._last_memory_mb - self._memory_baseline_mb > 200:
            return "memory leak"
        if self._check_failures.get("redis", 0) > 0:
            return "Redis connection instability"
        if self._check_failures.get("db_pool", 0) > 0:
            return "DB connection pool exhaustion"
        return "unknown — check subsystem heartbeats"

    # ── Circuit breaker per check ───────────────────────────

    def _record_check_failure(self, check_name: str):
        """Record a check failure for circuit breaker."""
        self._check_failures[check_name] = self._check_failures.get(check_name, 0) + 1
        # Disable check for 2 min after 5 consecutive failures
        if self._check_failures[check_name] >= 5:
            self._check_disabled_until[check_name] = time.time() + 120
            logger.warning("watchdog_circuit_breaker_tripped", check=check_name)

    def _is_circuit_broken(self, check_name: str) -> bool:
        """Check if a check's circuit breaker is tripped."""
        until = self._check_disabled_until.get(check_name, 0)
        if time.time() < until:
            return True
        # Reset if expired
        if until > 0:
            self._check_disabled_until.pop(check_name, None)
            self._check_failures[check_name] = 0
        return False

    # ── Alert throttling ────────────────────────────────────

    async def _throttled_alert(self, key: str, message: str):
        """Send alert with throttling — min 5 min between same key."""
        now = time.time()
        last = self._last_alerts.get(key, 0)
        if now - last < ALERT_THROTTLE_SEC:
            logger.debug("watchdog_alert_throttled", key=key)
            return
        self._last_alerts[key] = now
        await self._alert(message)

    async def _alert(self, message: str):
        """Send Telegram alert — always logged, sent to Telegram for Level 2+."""
        # Always log the alert so it appears in docker logs / Grafana Loki
        logger.warning("watchdog_alert", message=message[:200], service=self._service,
                        level=self._current_level, score=round(self._last_score, 1))
        if not self._chat_id:
            return
        try:
            from src.notifications.router import NotificationRouter, NotificationCategory
            notifier = NotificationRouter(self._telegram_bot, self._chat_id)
            # Level 2+ alerts are critical — force to Telegram
            force = self._current_level >= 2
            await notifier.send(message, NotificationCategory.INFRASTRUCTURE, force=force)
        except Exception as e:
            logger.warning("watchdog_alert_failed", error=str(e))

    # ── Public API ──────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current watchdog status for API endpoint."""
        now = time.time()
        subsystems = {}
        for subsystem, last_beat in self._heartbeats.items():
            age = now - last_beat
            subsystems[subsystem] = {
                "last_heartbeat": round(last_beat, 1),
                "age_seconds": round(age, 1),
                "healthy": age < HEARTBEAT_STALE_SEC,
            }
        return {
            "service": self._service,
            "running": self._running,
            "health_score": round(self._last_score, 1),
            "score_trend": round(self._score_trend(), 2),
            "level": self._current_level,
            "failure_count": self._failure_count,
            "memory_mb": round(self._last_memory_mb, 1),
            "event_loop_lag_sec": round(self._sentinel_lag, 2),
            "subsystems": subsystems,
            "circuit_breakers": {
                k: "open" if self._is_circuit_broken(k) else "closed"
                for k in self._check_failures
            },
        }
