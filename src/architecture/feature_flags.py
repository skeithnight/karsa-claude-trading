"""Feature flag framework — Redis-backed, instant rollback.

Every architectural component is independently toggleable.
Default: all flags OFF (no behavioral change until explicitly enabled).
"""
import os
import structlog

logger = structlog.get_logger(__name__)

# Default feature flags — all disabled by default
_DEFAULT_FLAGS = {
    "event_bus_enabled": False,
    "position_manager_enabled": False,
    "exit_engine_enabled": False,
    "decision_engine_enabled": False,
    "replay_enabled": False,
    "policy_engine_enabled": False,
    "workflow_enabled": False,
    "agent_runtime_enabled": False,
    # AODE (Asymmetric Opportunity Discovery Engine)
    "aode_discovery_enabled": False,
    "aode_research_enabled": False,
    "aode_scoring_enabled": False,
    "aode_monitoring_enabled": False,
}

class FeatureFlags:
    """Redis-backed feature flag store with in-memory fallback.

    ponytail: single shared instance, no DI ceremony.
    Redis keys live under karsa:feature_flags:<name>.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._local = dict(_DEFAULT_FLAGS)
        self._prefix = os.environ.get("REDIS_PREFIX", "karsa")

    def set_redis(self, redis_client):
        self._redis = redis_client

    def is_enabled(self, flag: str) -> bool:
        if flag not in _DEFAULT_FLAGS:
            logger.warning("unknown_feature_flag", flag=flag)
            return False
        if self._redis:
            try:
                val = self._redis.get(f"{self._prefix}:feature_flags:{flag}")
                if val is not None:
                    return val.decode() == "1"
            except Exception:
                pass  # fall through to local
        return self._local.get(flag, False)

    def disable(self, flag: str):
        self._local[flag] = False
        if self._redis:
            self._redis.set(f"{self._prefix}:feature_flags:{flag}", "0")
        logger.info("feature_flag_disabled", flag=flag)


# Module-level singleton
flags = FeatureFlags()
