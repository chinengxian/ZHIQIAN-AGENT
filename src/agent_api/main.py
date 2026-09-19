from fastapi import FastAPI

from agent_api.api.router import router
from agent_api.core.config import get_settings
from agent_api.core.lifespan import AgentFactory, SettingsFactory, create_lifespan
from agent_api.llm.strategy import create_agent_from_settings


def create_app(
    settings_factory: SettingsFactory = get_settings,
    agent_factory: AgentFactory = create_agent_from_settings,
) -> FastAPI:
    app = FastAPI(lifespan=create_lifespan(settings_factory, agent_factory))
    app.include_router(router)
    return app


app = create_app()
