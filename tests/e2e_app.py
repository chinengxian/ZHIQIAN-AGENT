import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import ModelProvider, Settings
from agent_api.llm.agent import LangChainAgentStream
from agent_api.main import create_app


class BrowserTestChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "browser-test-model"

    @staticmethod
    def _response(messages: list[BaseMessage]) -> str:
        user_messages = [
            str(message.content) for message in messages if isinstance(message, HumanMessage)
        ]
        if len(user_messages) == 1:
            return f"已记住：{user_messages[0]}"
        return f"你之前说：{user_messages[0]}；现在说：{user_messages[-1]}"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self._response(messages)))]
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        response = self._response(messages)
        for chunk in (response[:4], response[4:]):
            await asyncio.sleep(0.08)
            yield ChatGenerationChunk(message=AIMessageChunk(content=chunk))


def test_settings() -> Settings:
    return Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.com/v1"),
        openai_api_key=SecretStr("browser-test-key"),
        openai_model="browser-test-model",
        _env_file=None,  # type: ignore[call-arg]
    )


def test_agent(_settings: Settings) -> LangChainAgentStream:
    return LangChainAgentStream(BrowserTestChatModel())


app = create_app(settings_factory=test_settings, agent_factory=test_agent)
