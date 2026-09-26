from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import EmbeddingProvider, ModelProvider, Settings, get_settings
from agent_api.core.lifespan import create_lifespan
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.llm.agent import LangChainAgentStream
from agent_api.llm.strategy import create_agent_from_settings
from agent_api.main import create_app
from tests.fakes import FakeStreamAgent


def test_lifespan_and_app_default_to_the_unified_agent_factory() -> None:
    expected_defaults = (get_settings, create_agent_from_settings)

    assert create_lifespan.__defaults__ == expected_defaults
    assert create_app.__defaults__ == expected_defaults


def settings_for_provider(provider: ModelProvider) -> Settings:
    if provider is ModelProvider.OPENAI:
        return Settings(
            model_provider=provider,
            openai_base_url=AnyHttpUrl("https://example.test/v1"),
            openai_api_key=SecretStr("test-secret"),
            openai_model="test-model",
            _env_file=None,  # type: ignore[call-arg]
        )
    return Settings(
        model_provider=provider,
        anthropic_api_key=SecretStr("test-secret"),
        anthropic_model="test-model",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.mark.parametrize("provider", list(ModelProvider))
async def test_default_agent_factory_builds_selected_provider_agent(
    provider: ModelProvider,
) -> None:
    settings = settings_for_provider(provider)
    app = create_app(settings_factory=lambda: settings)

    async with LifespanManager(app):
        assert isinstance(app.state.chat_agent, LangChainAgentStream)


async def test_failed_agent_initialization_does_not_publish_partial_state(
    settings: Settings,
) -> None:
    def failing_agent_factory(_: Settings) -> FakeStreamAgent:
        raise RuntimeError("agent initialization failed")

    app = create_app(
        settings_factory=lambda: settings,
        agent_factory=failing_agent_factory,
    )

    with pytest.raises(RuntimeError, match="agent initialization failed"):
        async with LifespanManager(app):
            pass

    assert not hasattr(app.state, "settings")
    assert not hasattr(app.state, "chat_agent")


async def test_lifespan_initializes_and_cleans_shared_resources(settings: Settings) -> None:
    agent = FakeStreamAgent()
    app = create_app(settings_factory=lambda: settings, agent_factory=lambda _: agent)

    async with LifespanManager(app):
        assert app.state.settings is settings
        assert app.state.chat_agent is agent

    assert not hasattr(app.state, "settings")
    assert not hasattr(app.state, "chat_agent")


async def test_lifespan_publishes_and_closes_knowledge_application_services(
    tmp_path: Path,
) -> None:
    knowledge_settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("test-secret"),
        openai_model="test-model",
        knowledge_enabled=True,
        database_url=SecretStr("postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent"),
        storage_root=tmp_path / "uploads",
        embedding_provider=EmbeddingProvider.OPENAI,
        embedding_base_url=AnyHttpUrl("https://embedding.example.test/v1"),
        embedding_api_key=SecretStr("embedding-secret"),
        embedding_model="embedding-model",
        _env_file=None,  # type: ignore[call-arg]
    )

    class FakeKnowledgeStartup:
        def __init__(self) -> None:
            self.storage = LocalFileStorage(knowledge_settings.storage_root)
            self.closed = False

        async def start(self) -> None:
            self.storage.ensure_ready()

        async def close(self) -> None:
            self.closed = True

    startup = FakeKnowledgeStartup()
    app = FastAPI(
        lifespan=create_lifespan(
            lambda: knowledge_settings,
            lambda _: FakeStreamAgent(),
            knowledge_startup_factory=lambda _: startup,  # type: ignore[arg-type]
        )
    )

    async with LifespanManager(app):
        assert app.state.knowledge_database is not None
        assert app.state.knowledge_management is not None

    assert startup.closed is True
    assert not hasattr(app.state, "knowledge_database")
    assert not hasattr(app.state, "knowledge_management")
