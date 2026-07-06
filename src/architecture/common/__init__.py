"""Shared interfaces and base classes."""
from .base import AggregateRoot, ValueObject
from .interfaces import Repository

__all__ = ["AggregateRoot", "ValueObject", "Repository"]
