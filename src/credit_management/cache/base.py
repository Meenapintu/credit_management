from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class AsyncCacheBackend(ABC):
    """
    Minimal async cache abstraction used for frequently accessed data
    such as subscription plans and user credit balances.
    """

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

