import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from pymilvus import MilvusClient  # type: ignore[import-untyped]

from agent_api.core.config import RerankProvider, Settings, get_settings
from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.application.retrieval import KnowledgeRetrievalService
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.embedding.embedding import create_embedding_adapter
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.rerank.bge import LocalBGEReranker
from agent_api.knowledge.infrastructure.startup import (
    KnowledgeStartupFactory,
    create_knowledge_startup,
)
from agent_api.knowledge.tools.readonly import create_knowledge_tools
from agent_api.knowledge.wiki.service import WikiService
from agent_api.llm.agent import LangChainAgentStream
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
        knowledge_database = None
        knowledge_management = None
        knowledge_milvus = None
        knowledge_retrieval = None
        wiki_service = None
        try:
            # 知识库关闭时完全跳过 PostgreSQL、Redis、Milvus 和存储目录初始化。
            if settings.knowledge_enabled:
                knowledge_startup = knowledge_startup_factory(settings)
                await knowledge_startup.start()
                knowledge_database = DatabaseRuntime(settings.database_url.get_secret_value())
                knowledge_management = SqlAlchemyKnowledgeManagementService(
                    knowledge_database.session_factory,
                    knowledge_startup.storage,
                    settings,
                )
                wiki_service = WikiService(knowledge_database.session_factory)
            chat_agent = agent_factory(settings)
            if settings.knowledge_enabled and isinstance(chat_agent, LangChainAgentStream):
                assert knowledge_database is not None
                milvus_options = {"uri": settings.milvus_uri}
                if settings.milvus_token is not None:
                    milvus_options["token"] = settings.milvus_token.get_secret_value()
                knowledge_milvus = await asyncio.to_thread(MilvusClient, **milvus_options)
                reranker = (
                    LocalBGEReranker(
                        settings.rerank_model or "",
                        settings.rerank_device,
                        settings.rerank_batch_size,
                    )
                    if settings.rerank_provider == RerankProvider.LOCAL_BGE
                    else None
                )
                knowledge_retrieval = KnowledgeRetrievalService(
                    knowledge_database.session_factory,
                    MilvusChunkIndex(knowledge_milvus, settings.milvus_collection),
                    create_embedding_adapter(settings),
                    reranker,
                )
                chat_agent.enable_knowledge(
                    create_knowledge_tools(knowledge_retrieval, wiki_service)
                )
            # 所有构造均成功后再一次性发布共享资源。
            app.state.settings = settings
            app.state.chat_agent = chat_agent
            if knowledge_startup is not None:
                app.state.knowledge_startup = knowledge_startup
                app.state.knowledge_database = knowledge_database
                app.state.knowledge_management = knowledge_management
                app.state.wiki_service = wiki_service
            if knowledge_retrieval is not None:
                app.state.knowledge_retrieval = knowledge_retrieval
            yield
        finally:
            # 先撤销外部可见状态，再关闭底层连接，避免停机期间继续被读取。
            if hasattr(app.state, "knowledge_startup"):
                del app.state.knowledge_startup
            if hasattr(app.state, "knowledge_management"):
                del app.state.knowledge_management
            if hasattr(app.state, "wiki_service"):
                del app.state.wiki_service
            if hasattr(app.state, "knowledge_database"):
                del app.state.knowledge_database
            if hasattr(app.state, "knowledge_retrieval"):
                del app.state.knowledge_retrieval
            if hasattr(app.state, "chat_agent"):
                del app.state.chat_agent
            if hasattr(app.state, "settings"):
                del app.state.settings
            if knowledge_database is not None:
                await knowledge_database.close()
            if knowledge_milvus is not None:
                await asyncio.to_thread(knowledge_milvus.close)
            if knowledge_startup is not None:
                await knowledge_startup.close()

    return lifespan
