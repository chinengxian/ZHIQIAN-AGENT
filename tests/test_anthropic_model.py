import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import ModelProvider, Settings
from agent_api.llm.anthropic import AnthropicModelStrategy


@pytest.mark.parametrize("base_url", [None, AnyHttpUrl("https://anthropic.example.test")])
def test_anthropic_strategy_passes_selected_configuration(
    base_url: AnyHttpUrl | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingChatAnthropic:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("agent_api.llm.anthropic.ChatAnthropic", CapturingChatAnthropic)
    settings = Settings(
        model_provider=ModelProvider.ANTHROPIC,
        anthropic_api_key=SecretStr("anthropic-secret"),
        anthropic_model="claude-test",
        anthropic_base_url=base_url,
        _env_file=None,  # type: ignore[call-arg]
    )

    model = AnthropicModelStrategy().create_model(settings)

    assert captured["model"] == "claude-test"
    assert captured["api_key"] is settings.anthropic_api_key
    assert captured["stream_usage"] is False
    if base_url is None:
        assert "base_url" not in captured
    else:
        assert captured["base_url"] == "https://anthropic.example.test/"
    assert isinstance(model, CapturingChatAnthropic)


@pytest.mark.parametrize("base_url", [None, AnyHttpUrl("https://anthropic.example.test")])
def test_anthropic_strategy_constructs_real_chat_model(
    base_url: AnyHttpUrl | None,
) -> None:
    settings = Settings(
        model_provider=ModelProvider.ANTHROPIC,
        anthropic_api_key=SecretStr("anthropic-secret"),
        anthropic_model="claude-test",
        anthropic_base_url=base_url,
        _env_file=None,  # type: ignore[call-arg]
    )

    assert isinstance(AnthropicModelStrategy().create_model(settings), BaseChatModel)


@pytest.mark.parametrize(
    ("settings", "expected_message"),
    [
        (
            Settings(
                model_provider=ModelProvider.OPENAI,
                openai_api_key=SecretStr("openai-secret"),
                openai_model="test-model",
                openai_base_url=AnyHttpUrl("https://example.test/v1"),
                _env_file=None,  # type: ignore[call-arg]
            ),
            "Anthropic strategy requires the anthropic provider",
        ),
        (
            Settings.model_construct(
                model_provider=ModelProvider.ANTHROPIC,
                anthropic_api_key=SecretStr("anthropic-secret"),
            ),
            "Anthropic strategy requires anthropic_model",
        ),
        (
            Settings.model_construct(
                model_provider=ModelProvider.ANTHROPIC,
                anthropic_model="claude-test",
            ),
            "Anthropic strategy requires anthropic_api_key",
        ),
    ],
)
def test_anthropic_strategy_rejects_invalid_settings(
    settings: Settings,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message) as error:
        AnthropicModelStrategy().create_model(settings)

    assert "anthropic-secret" not in str(error.value)
