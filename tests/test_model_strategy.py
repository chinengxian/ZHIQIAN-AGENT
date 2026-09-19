from types import MappingProxyType
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ValidationError

import agent_api.llm.strategy as strategy
from agent_api.core.config import ModelProvider, Settings
from agent_api.llm.anthropic import AnthropicModelStrategy
from agent_api.llm.openai import OpenAIModelStrategy


class StubChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "stub"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unused"))])


@pytest.mark.parametrize(
    ("provider", "strategy_type"),
    [
        (ModelProvider.OPENAI, OpenAIModelStrategy),
        (ModelProvider.ANTHROPIC, AnthropicModelStrategy),
    ],
)
def test_registry_contains_expected_strategy(
    provider: ModelProvider,
    strategy_type: type[object],
) -> None:
    assert isinstance(strategy.STRATEGIES[provider], strategy_type)


def test_registry_is_read_only() -> None:
    assert isinstance(strategy.STRATEGIES, MappingProxyType)

    with pytest.raises(TypeError):
        strategy.STRATEGIES[ModelProvider.OPENAI] = AnthropicModelStrategy()  # type: ignore[index]


def test_registry_has_no_mutable_backing_reference() -> None:
    assert not hasattr(strategy, "_STRATEGIES")


def test_registry_exhaustively_maps_every_provider() -> None:
    assert set(strategy.STRATEGIES) == set(ModelProvider)


def test_unknown_provider_fails_safely_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[ModelProvider] = []

    def fail_if_called(settings: Settings) -> BaseChatModel:
        created.append(settings.model_provider)
        raise AssertionError("provider client must not be created")

    for model_strategy in strategy.STRATEGIES.values():
        monkeypatch.setattr(model_strategy, "create_model", fail_if_called)

    unknown_provider = "unknown-provider-secret"
    with pytest.raises(ValidationError) as captured:
        strategy.create_agent_from_settings(
            Settings(  # type: ignore[call-arg]
                model_provider=unknown_provider,  # type: ignore[arg-type]
                openai_base_url="https://example.test/v1",
                openai_api_key="openai-secret",  # type: ignore[arg-type]
                openai_model="openai-model",
                _env_file=None,
            )
        )

    error = captured.value
    provider_error = next(
        detail for detail in error.errors() if detail["loc"] == ("model_provider",)
    )
    assert provider_error["input"] is None
    assert unknown_provider not in str(error)
    assert unknown_provider not in repr(error)
    assert unknown_provider not in repr(error.errors())
    assert unknown_provider not in error.json()
    assert created == []


def test_unified_factory_uses_selected_strategy(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    model = StubChatModel()

    class CapturingStrategy:
        def create_model(self, received: Settings) -> BaseChatModel:
            captured["settings"] = received
            return model

    class CapturingAgent:
        def __init__(self, received: BaseChatModel) -> None:
            captured["model"] = received

    monkeypatch.setattr(
        strategy,
        "STRATEGIES",
        {ModelProvider.OPENAI: CapturingStrategy()},
    )
    monkeypatch.setattr(strategy, "LangChainAgentStream", CapturingAgent)

    result = strategy.create_agent_from_settings(settings)

    assert captured["settings"] is settings
    assert captured["model"] is model
    assert isinstance(result, CapturingAgent)
