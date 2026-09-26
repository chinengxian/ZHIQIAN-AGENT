import os
from pathlib import Path

from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import EmbeddingProvider, ModelProvider, Settings
from agent_api.llm.agent import LangChainAgentStream
from agent_api.main import create_app
from tests.integration.knowledge_chat_model import DeterministicKnowledgeModel
from tests.test_knowledge_management_integration import DATABASE_URL


def test_settings() -> Settings:
    return Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("local-test-key"),
        openai_model="deterministic-knowledge-test",
        knowledge_enabled=True,
        database_url=SecretStr(DATABASE_URL),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        storage_root=Path(os.environ["AGENT_STORAGE_ROOT"]),
        milvus_uri="http://127.0.0.1:19530",
        embedding_provider=EmbeddingProvider.OPENAI,
        embedding_base_url=AnyHttpUrl(os.environ["AGENT_EMBEDDING_BASE_URL"]),
        embedding_api_key=SecretStr("local-embedding-key"),
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        _env_file=None,  # type: ignore[call-arg]
    )


app = create_app(
    settings_factory=test_settings,
    agent_factory=lambda _: LangChainAgentStream(DeterministicKnowledgeModel()),
)
