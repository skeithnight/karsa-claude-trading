"""Notification routing — separates business alerts from infrastructure noise."""
from src.notifications.router import NotificationCategory, NotificationRouter

__all__ = ["NotificationCategory", "NotificationRouter"]
