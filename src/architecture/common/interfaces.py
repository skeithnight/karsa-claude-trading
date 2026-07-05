"""Repository and service interfaces."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Optional, List

T = TypeVar("T")


class Repository(ABC, Generic[T]):
    """Abstract repository — persistence isolation for domain services."""

    @abstractmethod
    async def get_by_id(self, id: str) -> Optional[T]: ...

    @abstractmethod
    async def save(self, entity: T) -> None: ...

    @abstractmethod
    async def delete(self, id: str) -> None: ...

    @abstractmethod
    async def list_all(self) -> List[T]: ...
