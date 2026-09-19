from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel

from agent_api.core.config import ModelProvider, Settings


class AnthropicModelStrategy:
    def create_model(self, settings: Settings) -> BaseChatModel:
        if settings.model_provider is not ModelProvider.ANTHROPIC:
            raise ValueError("Anthropic strategy requires the anthropic provider")
        if settings.anthropic_model is None:
            raise ValueError("Anthropic strategy requires anthropic_model")
        if settings.anthropic_api_key is None:
            raise ValueError("Anthropic strategy requires anthropic_api_key")
        if settings.anthropic_base_url is None:
            # `model` is the public Pydantic alias, but the generated signature exposes model_name.
            return ChatAnthropic(  # type: ignore[call-arg]
                model=settings.anthropic_model,
                api_key=settings.anthropic_api_key,
                stream_usage=False,
            )
        return ChatAnthropic(  # type: ignore[call-arg]
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            base_url=str(settings.anthropic_base_url),
            stream_usage=False,
        )
