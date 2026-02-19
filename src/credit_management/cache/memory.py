from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from .base import AsyncCacheBackend


class InMemoryAsyncCache(AsyncCacheBackend):
    """
    Simple in-memory cache with optional TTL.
    Intended for tests and local development.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[Any, Optional[float]]] = {}

    async def get(self, key: str) -> Optional[Any]:
        value_ttl = self._store.get(key)
        if value_ttl is None:
            return None
        value, expires_at = value_ttl
        if expires_at is not None and expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        self._store[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

