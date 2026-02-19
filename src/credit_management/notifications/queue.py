from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class AsyncNotificationQueue(ABC):
    """
    Abstract async message queue for dispatching user notifications.
    Concrete implementations could use Redis, RabbitMQ, Kafka, etc.
    """

    @abstractmethod
    async def enqueue(self, payload: Dict[str, Any]) -> None:
        ...


class InMemoryNotificationQueue(AsyncNotificationQueue):
    """
    In-memory queue used for tests and as a reference implementation.
    """

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def enqueue(self, payload: Dict[str, Any]) -> None:
        self.messages.append(payload)

