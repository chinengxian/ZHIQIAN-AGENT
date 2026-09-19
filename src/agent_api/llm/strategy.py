from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from agent_api.core.config import ModelProvider, Settings
from agent_api.llm.agent import LangChainAgentStream
from agent_api.llm.anthropic import AnthropicModelStrategy
from agent_api.llm.openai import OpenAIModelStrategy
from agent_api.llm.protocol import StreamAgent


class ModelStrategy(Protocol):
    def create_model(self, settings: Settings) -> BaseChatModel: ...


STRATEGIES: Mapping[ModelProvider, ModelStrategy] = MappingProxyType(
    {
        ModelProvider.OPENAI: OpenAIModelStrategy(),
        ModelProvider.ANTHROPIC: AnthropicModelStrategy(),
    }
)


def create_agent_from_settings(settings: Settings) -> StreamAgent:
    model = STRATEGIES[settings.model_provider].create_model(settings)
    return LangChainAgentStream(model)
