import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from agent_api.llm.agent import LangChainAgentStream
from agent_api.llm.protocol import StreamAgent

CONVERSATION_A = UUID("82de55a8-6065-4eeb-84af-079ea2e2b2c5")
CONVERSATION_B = UUID("67724b7e-a88c-4a9e-9272-7c4168fc7e25")


class ScriptedChatModel(BaseChatModel):
    modes: list[str]
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-test-model"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unused"))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.seen_messages.append(list(messages))
        mode = self.modes.pop(0)
        if mode == "fail-before":
            raise RuntimeError("provider details")
        if mode == "fail-mid":
            yield ChatGenerationChunk(message=AIMessageChunk(content="partial"))
            raise RuntimeError("provider details")
        if mode == "wait":
            yield ChatGenerationChunk(message=AIMessageChunk(content="partial"))
            await asyncio.sleep(30)
            return
        yield ChatGenerationChunk(message=AIMessageChunk(content=mode))


class EventControlledChatModel(BaseChatModel):
    entered: dict[str, asyncio.Event]
    releases: dict[str, asyncio.Event]

    @property
    def _llm_type(self) -> str:
        return "event-controlled-test-model"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unused"))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        prompt = str(messages[-1].content)
        self.entered[prompt].set()
        await self.releases[prompt].wait()
        yield ChatGenerationChunk(message=AIMessageChunk(content=f"reply to {prompt}"))


async def collect(adapter: StreamAgent, message: str, conversation_id: UUID) -> list[str]:
    return [chunk async for chunk in adapter.stream(message, conversation_id)]


async def test_agent_restores_history_for_the_same_conversation() -> None:
    model = ScriptedChatModel(modes=["first reply", "second reply"])
    adapter = LangChainAgentStream(model)

    assert await collect(adapter, "remember coffee", CONVERSATION_A) == ["first reply"]
    assert await collect(adapter, "what do I like?", CONVERSATION_A) == ["second reply"]

    assert [message.content for message in model.seen_messages[1]] == [
        "remember coffee",
        "first reply",
        "what do I like?",
    ]


async def test_agent_isolates_different_conversations() -> None:
    model = ScriptedChatModel(modes=["reply a", "reply b"])
    adapter = LangChainAgentStream(model)

    await collect(adapter, "message a", CONVERSATION_A)
    await collect(adapter, "message b", CONVERSATION_B)

    assert [message.content for message in model.seen_messages[1]] == ["message b"]


@pytest.mark.parametrize("failure_mode", ["fail-before", "fail-mid"])
async def test_agent_rolls_back_failed_turn(
    failure_mode: str,
) -> None:
    model = ScriptedChatModel(modes=["kept reply", failure_mode, "next reply"])
    adapter = LangChainAgentStream(model)
    await collect(adapter, "kept message", CONVERSATION_A)

    with pytest.raises(RuntimeError, match="provider details"):
        await collect(adapter, "failed message", CONVERSATION_A)
    await collect(adapter, "next message", CONVERSATION_A)

    assert [message.content for message in model.seen_messages[2]] == [
        "kept message",
        "kept reply",
        "next message",
    ]


async def test_agent_rolls_back_cancelled_turn() -> None:
    model = ScriptedChatModel(modes=["kept reply", "wait", "next reply"])
    adapter = LangChainAgentStream(model)
    await collect(adapter, "kept message", CONVERSATION_A)

    chunks: list[str] = []
    partial_received = asyncio.Event()

    async def consume_cancelled_turn() -> None:
        async for chunk in adapter.stream("cancelled message", CONVERSATION_A):
            chunks.append(chunk)
            partial_received.set()

    task = asyncio.create_task(consume_cancelled_turn())
    await asyncio.wait_for(partial_received.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert chunks == ["partial"]
    await collect(adapter, "next message", CONVERSATION_A)

    assert [message.content for message in model.seen_messages[2]] == [
        "kept message",
        "kept reply",
        "next message",
    ]


async def test_same_conversation_concurrent_requests_are_serialized() -> None:
    entered = {"first": asyncio.Event(), "second": asyncio.Event()}
    releases = {"first": asyncio.Event(), "second": asyncio.Event()}
    model = EventControlledChatModel(entered=entered, releases=releases)
    adapter = LangChainAgentStream(model)

    first = asyncio.create_task(collect(adapter, "first", CONVERSATION_A))
    await asyncio.wait_for(entered["first"].wait(), timeout=1)
    second = asyncio.create_task(collect(adapter, "second", CONVERSATION_A))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(entered["second"].wait(), timeout=0.05)

    releases["first"].set()
    assert await asyncio.wait_for(first, timeout=1) == ["reply to first"]
    await asyncio.wait_for(entered["second"].wait(), timeout=1)
    releases["second"].set()
    assert await asyncio.wait_for(second, timeout=1) == ["reply to second"]


async def test_different_conversations_can_call_model_concurrently() -> None:
    entered = {"first": asyncio.Event(), "second": asyncio.Event()}
    releases = {"first": asyncio.Event(), "second": asyncio.Event()}
    model = EventControlledChatModel(entered=entered, releases=releases)
    adapter = LangChainAgentStream(model)

    first = asyncio.create_task(collect(adapter, "first", CONVERSATION_A))
    await asyncio.wait_for(entered["first"].wait(), timeout=1)
    second = asyncio.create_task(collect(adapter, "second", CONVERSATION_B))

    await asyncio.wait_for(entered["second"].wait(), timeout=1)
    releases["first"].set()
    releases["second"].set()
    assert await asyncio.wait_for(first, timeout=1) == ["reply to first"]
    assert await asyncio.wait_for(second, timeout=1) == ["reply to second"]
