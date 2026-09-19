from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID


class StreamAgent(Protocol):
    def stream(self, message: str, conversation_id: UUID) -> AsyncIterator[str]: ...
