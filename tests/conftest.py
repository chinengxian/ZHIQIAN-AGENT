from collections.abc import Iterator

import pytest
from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import DotEnvSettingsSource

from agent_api.core.config import ModelProvider, Settings, get_settings

AGENT_ENV_NAMES = (
    "AGENT_APP_NAME",
    "AGENT_MODEL_PROVIDER",
    "AGENT_OPENAI_BASE_URL",
    "AGENT_OPENAI_API_KEY",
    "AGENT_OPENAI_MODEL",
    "AGENT_ANTHROPIC_API_KEY",
    "AGENT_ANTHROPIC_MODEL",
    "AGENT_ANTHROPIC_BASE_URL",
)

_collection_dotenv_guard = pytest.MonkeyPatch()


def reject_configured_env_file(source: DotEnvSettingsSource) -> dict[str, str | None]:
    if source.env_file is not None:
        raise AssertionError(
            "Tests must construct Settings with _env_file=None; dotenv reads are forbidden"
        )
    return {}


def pytest_configure() -> None:
    _collection_dotenv_guard.setattr(
        DotEnvSettingsSource,
        "_read_env_files",
        reject_configured_env_file,
    )


def pytest_unconfigure() -> None:
    _collection_dotenv_guard.undo()


@pytest.fixture(autouse=True)
def forbid_dotenv_file_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_files", reject_configured_env_file)


@pytest.fixture(autouse=True)
def clear_agent_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    for name in AGENT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("test-secret"),
        openai_model="test-model",
        _env_file=None,  # type: ignore[call-arg]
    )
