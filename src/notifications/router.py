"""Centralized notification router.

Telegram receives ONLY business actions (trades, regime shifts, manual commands).
Infrastructure/risk/system alerts go to structured logging for Grafana/Loki.
Emergency risk alerts use force=True to bypass filtering.
"""
from __future__ import annotations

import logging
import sys
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum
    class StrEnum(str, Enum):
        pass

logger = logging.getLogger(__name__)


class NotificationCategory(StrEnum):
    ASM_TRADE = "ASM_TRADE"
    ASM_REGIME = "ASM_REGIME"
    MANUAL_COMMAND = "MANUAL_COMMAND"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    RISK_ALERT = "RISK_ALERT"
    SYSTEM_ERROR = "SYSTEM_ERROR"


# Categories allowed to reach Telegram
_TELEGRAM_ALLOWED = frozenset({
    NotificationCategory.ASM_TRADE,
    NotificationCategory.ASM_REGIME,
    NotificationCategory.MANUAL_COMMAND,
})


class NotificationRouter:
    """Routes notifications: Telegram for business, logging for everything else."""

    def __init__(self, telegram_bot, chat_id: int, redis_client=None):
        self._bot = telegram_bot
        self._chat_id = chat_id
        self._redis = redis_client

    async def send(
        self,
        message: str,
        category: NotificationCategory,
        *,
        force: bool = False,
        parse_mode: str = "HTML",
        reply_markup=None,
    ) -> None:
        """Route a notification by category.

        Args:
            message: The message text.
            category: NotificationCategory enum value.
            force: If True, send to Telegram regardless of category (for emergencies).
            parse_mode: Telegram parse mode.
            reply_markup: Optional Telegram InlineKeyboardMarkup.
        """
        # Always log for Grafana/Loki
        # ponytail: stdlib logging.Logger._log() rejects unknown kwargs.
        # Embed category in message string instead.
        logger.info(
            "notification category=%s %s",
            category.value,
            message[:120],
        )

        # Decide whether to send to Telegram
        should_send = force or category in _TELEGRAM_ALLOWED
        if not should_send:
            return

        if not self._bot or not self._chat_id:
            return

        # Respect alerts_enabled toggle for trade alerts only
        if category == NotificationCategory.ASM_TRADE and self._redis:
            try:
                enabled = await self._redis.get("karsa:alerts_enabled")
                if enabled is not None and enabled.decode() == "false":
                    return
            except Exception:
                pass  # If Redis fails, send anyway

        try:
            kwargs = {
                "chat_id": self._chat_id,
                "text": message,
                "parse_mode": parse_mode,
            }
            if reply_markup:
                kwargs["reply_markup"] = reply_markup
            await self._bot.send_message(**kwargs)
        except Exception as e:
            logger.warning("notification_send_failed", category=category, error=str(e))
