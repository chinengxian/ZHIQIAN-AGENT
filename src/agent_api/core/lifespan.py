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
    """创建应用生命周期，并保证知识资源先检查、后发布、逆序释放。

    只有全部启动检查和 Agent 构造成功后，资源才写入 ``app.state``。
    因此任何中途失败都不会让请求看到半初始化状态。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = settings_factory()
        knowledge_startup = None
        try:
            # 知识库关闭时完全跳过 PostgreSQL、Redis、Milvus 和存储目录初始化。
            if settings.knowledge_enabled:
                knowledge_startup = knowledge_startup_factory(settings)
                await knowledge_startup.start()
            chat_agent = agent_factory(settings)
            # 所有构造均成功后再一次性发布共享资源。
            app.state.settings = settings
            app.state.chat_agent = chat_agent
            if knowledge_startup is not None:
                app.state.knowledge_startup = knowledge_startup
            yield
        finally:
            # 先撤销外部可见状态，再关闭底层连接，避免停机期间继续被读取。
            if hasattr(app.state, "knowledge_startup"):
                del app.state.knowledge_startup
            if hasattr(app.state, "chat_agent"):
                del app.state.chat_agent
            if hasattr(app.state, "settings"):
                del app.state.settings
            if knowledge_startup is not None:
                await knowledge_startup.close()

    return lifespan
