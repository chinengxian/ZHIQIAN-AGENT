import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, cast
from uuid import UUID

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES


class LangChainAgentStream:
    def __init__(self, client: BaseChatModel) -> None:
        self._agent = create_agent(
            model=client,
            tools=[],
            checkpointer=InMemorySaver(),
        )
        self._locks: dict[str, asyncio.Lock] = {}

    async def _messages(self, config: RunnableConfig) -> list[BaseMessage]:
        state = await self._agent.aget_state(config)
        messages = state.values.get("messages", [])
        return [message for message in messages if isinstance(message, BaseMessage)]

    async def _rollback(
        self,
        config: RunnableConfig,
        baseline: list[BaseMessage],
    ) -> None:
        rollback_messages: list[BaseMessage] = [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *baseline,
        ]
        await self._agent.aupdate_state(config, {"messages": rollback_messages})

    async def stream(self, message: str, conversation_id: UUID) -> AsyncIterator[str]:
        thread_id = str(conversation_id)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        lock = self._locks.setdefault(thread_id, asyncio.Lock())

        async with lock:
            baseline = await self._messages(config)
            try:
                async for item in self._agent.astream(
                    {"messages": [HumanMessage(content=message)]},
                    config=config,
                    stream_mode="messages",
                ):
                    chunk, metadata = cast(tuple[BaseMessage, dict[str, Any]], item)
                    if (
                        isinstance(chunk, AIMessageChunk)
                        and metadata.get("langgraph_node") == "model"
                        and isinstance(chunk.content, str)
                        and chunk.content
                    ):
                        yield chunk.content
            except BaseException:
                with suppress(BaseException):
                    await self._rollback(config, baseline)
                raise
