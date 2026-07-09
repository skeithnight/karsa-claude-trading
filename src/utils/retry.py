"""Karsa Trading System - Async Retry Utility

Provides retry decorator with exponential backoff for critical operations.
"""

import asyncio
import functools
from typing import Callable, Any

from src.utils.logging import get_logger

logger = get_logger("retry")


def async_retry(max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 10.0,
                exceptions: tuple = (Exception,), log_label: str = "operation"):
    """Decorator for async functions with exponential backoff retry.

    Args:
        max_attempts: Maximum number of attempts (default 3)
        base_delay: Base delay in seconds (default 1.0)
        max_delay: Maximum delay in seconds (default 10.0)
        exceptions: Tuple of exception types to catch (default: all Exception)
        log_label: Label for logging (default: "operation")
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(f"{log_label}_retry",
                                       attempt=attempt + 1,
                                       max_attempts=max_attempts,
                                       delay=delay,
                                       error=str(e))
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"{log_label}_failed",
                                     attempts=max_attempts,
                                     error=str(e))
            raise last_error
        return wrapper
    return decorator
