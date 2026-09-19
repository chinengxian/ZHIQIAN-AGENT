from pathlib import Path

import pytest
from pydantic import AnyHttpUrl, SecretStr, ValidationError

from agent_api.core.config import ModelProvider, Settings


def make_settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg,arg-type]


def test_pytest_guard_rejects_dotenv_loading() -> None:
    env_file = Path(__file__).with_name("__pytest_dotenv_guard_intentionally_missing__.env")
    assert not env_file.exists()

    with pytest.raises(AssertionError, match="dotenv reads are forbidden"):
        Settings(
            model_provider=ModelProvider.OPENAI,
            openai_base_url=AnyHttpUrl("https://example.test/v1"),
            openai_api_key=SecretStr("openai-secret"),
            openai_model="openai-model",
            _env_file=env_file,  # type: ignore[call-arg]
        )


def test_e2e_settings_factory_disables_dotenv_loading() -> None:
    from tests.e2e_app import test_settings as make_e2e_settings

    settings = make_e2e_settings()

    assert settings.openai_model == "browser-test-model"


def test_settings_require_model_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_MODEL_PROVIDER", raising=False)

    with pytest.raises(ValidationError, match="model_provider"):
        make_settings()


def test_openai_configuration_only_requires_openai_fields() -> None:
    settings = make_settings(
        model_provider="openai",
        openai_base_url="https://example.test/v1",
        openai_api_key="openai-secret",
        openai_model="openai-model",
    )

    assert settings.model_provider is ModelProvider.OPENAI
    assert settings.anthropic_api_key is None


def test_anthropic_configuration_only_requires_anthropic_fields() -> None:
    settings = make_settings(
        model_provider="anthropic",
        anthropic_api_key="anthropic-secret",
        anthropic_model="claude-test",
    )

    assert settings.model_provider is ModelProvider.ANTHROPIC
    assert settings.openai_api_key is None
    assert settings.anthropic_base_url is None


@pytest.mark.parametrize(
    ("provider", "kwargs", "unselected_url_field"),
    [
        (
            "openai",
            {
                "openai_base_url": "https://example.test/v1",
                "openai_api_key": "openai-secret",
                "openai_model": "openai-model",
                "anthropic_base_url": "not-a-url",
            },
            "anthropic_base_url",
        ),
        (
            "anthropic",
            {
                "anthropic_api_key": "anthropic-secret",
                "anthropic_model": "claude-test",
                "openai_base_url": "not-a-url",
            },
            "openai_base_url",
        ),
    ],
)
def test_unselected_provider_does_not_validate_base_url(
    provider: str,
    kwargs: dict[str, str],
    unselected_url_field: str,
) -> None:
    settings = make_settings(model_provider=provider, **kwargs)

    assert settings.model_provider.value == provider
    assert getattr(settings, unselected_url_field) == "not-a-url"


@pytest.mark.parametrize(
    ("provider", "kwargs", "selected_url_field"),
    [
        (
            "openai",
            {
                "openai_base_url": "selected-openai-secret-url",
                "openai_api_key": "openai-secret",
                "openai_model": "openai-model",
            },
            "openai_base_url",
        ),
        (
            "anthropic",
            {
                "anthropic_api_key": "anthropic-secret",
                "anthropic_model": "claude-test",
                "anthropic_base_url": "selected-anthropic-secret-url",
            },
            "anthropic_base_url",
        ),
    ],
)
def test_selected_provider_rejects_malformed_base_url_with_sanitized_field_error(
    provider: str,
    kwargs: dict[str, str],
    selected_url_field: str,
) -> None:
    malformed_url = str(kwargs[selected_url_field])

    with pytest.raises(ValidationError) as captured:
        make_settings(model_provider=provider, **kwargs)

    error = captured.value
    field_errors = [detail for detail in error.errors() if detail["loc"] == (selected_url_field,)]
    assert len(field_errors) == 1
    assert field_errors[0]["input"] is None
    assert malformed_url not in str(error)
    assert malformed_url not in repr(error)
    assert malformed_url not in repr(error.errors())
    assert malformed_url not in error.json()


@pytest.mark.parametrize(
    ("provider", "kwargs", "missing_field"),
    [
        ("openai", {"openai_api_key": "key", "openai_model": "model"}, "openai_base_url"),
        (
            "openai",
            {"openai_base_url": "https://example.test/v1", "openai_model": "model"},
            "openai_api_key",
        ),
        (
            "openai",
            {"openai_base_url": "https://example.test/v1", "openai_api_key": "key"},
            "openai_model",
        ),
        (
            "anthropic",
            {"anthropic_model": "claude-test"},
            "anthropic_api_key",
        ),
        (
            "anthropic",
            {"anthropic_api_key": "key"},
            "anthropic_model",
        ),
    ],
)
def test_selected_provider_reports_missing_fields(
    provider: str,
    kwargs: dict[str, str],
    missing_field: str,
) -> None:
    with pytest.raises(ValidationError, match=missing_field):
        make_settings(model_provider=provider, **kwargs)


@pytest.mark.parametrize(
    ("provider", "kwargs", "field"),
    [
        (
            "openai",
            {
                "openai_base_url": "https://example.test/v1",
                "openai_api_key": "   ",
                "openai_model": "model",
            },
            "openai_api_key",
        ),
        (
            "openai",
            {
                "openai_base_url": "https://example.test/v1",
                "openai_api_key": "key",
                "openai_model": "   ",
            },
            "openai_model",
        ),
        (
            "anthropic",
            {"anthropic_api_key": "   ", "anthropic_model": "claude-test"},
            "anthropic_api_key",
        ),
        (
            "anthropic",
            {"anthropic_api_key": "key", "anthropic_model": "   "},
            "anthropic_model",
        ),
    ],
)
def test_selected_provider_rejects_blank_secrets_and_models(
    provider: str,
    kwargs: dict[str, str],
    field: str,
) -> None:
    with pytest.raises(ValidationError, match=field):
        make_settings(model_provider=provider, **kwargs)


@pytest.mark.parametrize(
    ("provider", "kwargs"),
    [
        (
            "openai",
            {
                "openai_base_url": "https://example.test/v1",
                "openai_api_key": "key",
                "openai_model": "model",
                "anthropic_model": "   ",
            },
        ),
        (
            "anthropic",
            {
                "anthropic_api_key": "key",
                "anthropic_model": "claude-test",
                "openai_model": "   ",
            },
        ),
    ],
)
def test_unselected_provider_allows_blank_model(
    provider: str,
    kwargs: dict[str, str],
) -> None:
    settings = make_settings(model_provider=provider, **kwargs)

    assert settings.model_provider.value == provider


def test_settings_load_selected_provider_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_ANTHROPIC_API_KEY", "secret-value")
    monkeypatch.setenv("AGENT_ANTHROPIC_MODEL", "claude-test")

    settings = make_settings()

    assert settings.model_provider is ModelProvider.ANTHROPIC
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "secret-value"
    assert "secret-value" not in repr(settings)


@pytest.mark.parametrize("source", ["constructor", "environment"])
def test_validation_errors_redact_secrets(
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "must-not-appear"

    kwargs: dict[str, str] = {}
    if source == "constructor":
        kwargs = {
            "model_provider": "anthropic",
            "anthropic_api_key": secret,
        }
    else:
        monkeypatch.setenv("AGENT_MODEL_PROVIDER", "anthropic")
        monkeypatch.setenv("AGENT_ANTHROPIC_API_KEY", secret)

    with pytest.raises(ValidationError) as captured:
        make_settings(**kwargs)

    error = captured.value
    assert "anthropic_model" in str(error)
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in repr(error.errors())
    assert secret not in error.json()


@pytest.mark.parametrize("source", ["constructor", "environment"])
def test_native_validation_errors_redact_secrets_when_provider_is_missing(
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "native-error-must-not-appear"

    kwargs: dict[str, str] = {}
    if source == "constructor":
        kwargs = {"openai_api_key": secret}
    else:
        monkeypatch.setenv("AGENT_OPENAI_API_KEY", secret)

    with pytest.raises(ValidationError) as captured:
        make_settings(**kwargs)

    error = captured.value
    provider_error = next(
        detail for detail in error.errors() if detail["loc"] == ("model_provider",)
    )
    assert provider_error["msg"] == "Field required"
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in repr(error.errors())
    assert secret not in error.json()
