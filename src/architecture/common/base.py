"""Base classes for domain modeling."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone
import uuid


@dataclass
class ValueObject:
    """Immutable value object. Subclass and add fields."""
    pass


@dataclass
class AggregateRoot:
    """Base for domain aggregates with identity and version tracking.

    ponytail: lightweight — no event sourcing machinery, just identity + version.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def bump_version(self):
        self.version += 1
        self.updated_at = datetime.now(timezone.utc)
