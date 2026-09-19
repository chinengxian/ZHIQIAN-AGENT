import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import ModelProvider, Settings
from agent_api.llm.openai import OpenAIModelStrategy


def test_openai_strategy_passes_selected_configuration(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("agent_api.llm.openai.ChatOpenAI", CapturingChatOpenAI)

    model = OpenAIModelStrategy().create_model(settings)

    assert captured["model"] == "test-model"
    assert captured["api_key"] is settings.openai_api_key
    assert captured["base_url"] == "https://example.test/v1"
    assert captured["stream_usage"] is False
    assert isinstance(model, CapturingChatOpenAI)


def test_openai_strategy_constructs_real_chat_model(settings: Settings) -> None:
    assert isinstance(OpenAIModelStrategy().create_model(settings), BaseChatModel)


@pytest.mark.parametrize(
    ("settings", "expected_message"),
    [
        (
            Settings(
                model_provider=ModelProvider.ANTHROPIC,
                anthropic_api_key=SecretStr("anthropic-secret"),
                anthropic_model="claude-test",
                _env_file=None,  # type: ignore[call-arg]
            ),
            "OpenAI strategy requires the openai provider",
        ),
        (
            Settings.model_construct(
                model_provider=ModelProvider.OPENAI,
                openai_api_key=SecretStr("openai-secret"),
                openai_base_url=AnyHttpUrl("https://example.test/v1"),
            ),
            "OpenAI strategy requires openai_model",
        ),
        (
            Settings.model_construct(
                model_provider=ModelProvider.OPENAI,
                openai_model="test-model",
                openai_base_url=AnyHttpUrl("https://example.test/v1"),
            ),
            "OpenAI strategy requires openai_api_key",
        ),
        (
            Settings.model_construct(
                model_provider=ModelProvider.OPENAI,
                openai_api_key=SecretStr("openai-secret"),
                openai_model="test-model",
            ),
            "OpenAI strategy requires openai_base_url",
        ),
    ],
)
def test_openai_strategy_rejects_invalid_settings(
    settings: Settings,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message) as error:
        OpenAIModelStrategy().create_model(settings)

    assert "openai-secret" not in str(error.value)
