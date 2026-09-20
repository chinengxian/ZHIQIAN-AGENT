from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from agent_api.core.config import Settings, get_settings
from agent_api.knowledge.infrastructure.startup import (
    KnowledgeStartupFactory,
    create_knowledge_startup,
)
from agent_api.llm.protocol import StreamAgent
from agent_api.llm.strategy import create_agent_from_settings

SettingsFactory = Callable[[], Settings]
AgentFactory = Callable[[Settings], StreamAgent]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_lifespan(
    settings_factory: SettingsFactory = get_settings,
    agent_factory: AgentFactory = create_agent_from_settings,
    *,
    knowledge_startup_factory: KnowledgeStartupFactory = create_knowledge_startup,
) -> Lifespan:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = settings_factory()
        knowledge_startup = None
        try:
            if settings.knowledge_enabled:
                knowledge_startup = knowledge_startup_factory(settings)
                await knowledge_startup.start()
            chat_agent = agent_factory(settings)
            app.state.settings = settings
            app.state.chat_agent = chat_agent
            if knowledge_startup is not None:
                app.state.knowledge_startup = knowledge_startup
            yield
        finally:
            if hasattr(app.state, "knowledge_startup"):
                del app.state.knowledge_startup
            if hasattr(app.state, "chat_agent"):
                del app.state.chat_agent
            if hasattr(app.state, "settings"):
                del app.state.settings
            if knowledge_startup is not None:
                await knowledge_startup.close()

    return lifespan
