from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from agent_api.core.config import Settings, get_settings
from agent_api.llm.protocol import StreamAgent
from agent_api.llm.strategy import create_agent_from_settings

SettingsFactory = Callable[[], Settings]
AgentFactory = Callable[[Settings], StreamAgent]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_lifespan(
    settings_factory: SettingsFactory = get_settings,
    agent_factory: AgentFactory = create_agent_from_settings,
) -> Lifespan:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = settings_factory()
        chat_agent = agent_factory(settings)
        app.state.settings = settings
        app.state.chat_agent = chat_agent
        try:
            yield
        finally:
            del app.state.chat_agent
            del app.state.settings

    return lifespan
