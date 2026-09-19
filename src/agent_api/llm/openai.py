from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from agent_api.core.config import ModelProvider, Settings


class OpenAIModelStrategy:
    def create_model(self, settings: Settings) -> BaseChatModel:
        if settings.model_provider is not ModelProvider.OPENAI:
            raise ValueError("OpenAI strategy requires the openai provider")
        if settings.openai_model is None:
            raise ValueError("OpenAI strategy requires openai_model")
        if settings.openai_api_key is None:
            raise ValueError("OpenAI strategy requires openai_api_key")
        if settings.openai_base_url is None:
            raise ValueError("OpenAI strategy requires openai_base_url")
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=str(settings.openai_base_url),
            stream_usage=False,
        )
