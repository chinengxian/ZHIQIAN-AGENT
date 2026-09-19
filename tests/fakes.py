from collections.abc import AsyncIterator, Sequence
from uuid import UUID


class FakeStreamAgent:
    def __init__(
        self,
        chunks: Sequence[str] = (),
        *,
        fail_before_first: bool = False,
        fail_after_chunks: bool = False,
    ) -> None:
        self.chunks = chunks
        self.fail_before_first = fail_before_first
        self.fail_after_chunks = fail_after_chunks
        self.calls: list[tuple[str, UUID]] = []

    async def stream(self, message: str, conversation_id: UUID) -> AsyncIterator[str]:
        self.calls.append((message, conversation_id))
        if self.fail_before_first:
            raise RuntimeError("provider details must stay private")
        for chunk in self.chunks:
            yield chunk
        if self.fail_after_chunks:
            raise RuntimeError("provider details must stay private")
