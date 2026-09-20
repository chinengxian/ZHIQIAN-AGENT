from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from agent_api.core.config import ModelProvider, Settings


def knowledge_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "model_provider": "openai",
        "openai_base_url": "https://chat.example.test/v1",
        "openai_api_key": "chat-secret",
        "openai_model": "chat-model",
        "knowledge_enabled": True,
        "database_url": "postgresql+asyncpg://agent:secret@db:5432/agent",
        "redis_url": "redis://redis:6379/0",
        "storage_root": tmp_path / "uploads",
        "milvus_uri": "http://milvus:19530",
        "milvus_token": "milvus-secret",
        "milvus_collection": "knowledge_chunks",
        "embedding_provider": "openai",
        "embedding_base_url": "https://embedding.example.test/v1",
        "embedding_api_key": "embedding-secret",
        "embedding_model": "text-embedding-test",
        "embedding_dimension": 1024,
        "embedding_batch_size": 32,
        "rerank_provider": "none",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg,arg-type]


def test_knowledge_configuration_keeps_credentials_secret(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path)

    assert settings.knowledge_enabled is True
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.redis_url.get_secret_value() == "redis://redis:6379/0"
    assert settings.embedding_api_key is not None
    assert settings.embedding_api_key.get_secret_value() == "embedding-secret"
    assert settings.embedding_dimension == 1024
    assert "embedding-secret" not in repr(settings)
    assert "milvus-secret" not in repr(settings)
    assert "secret@db" not in repr(settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", "sqlite:///local.db"),
        ("redis_url", "http://redis:6379/0"),
        ("embedding_dimension", 0),
        ("embedding_batch_size", 0),
        ("max_upload_bytes", 0),
    ],
)
def test_knowledge_configuration_rejects_unsafe_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match=field):
        knowledge_settings(tmp_path, **{field: value})


def test_enabled_knowledge_requires_embedding_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as captured:
        knowledge_settings(tmp_path, embedding_api_key=None)

    error = captured.value
    assert "embedding_api_key" in str(error)
    assert "chat-secret" not in str(error)
    assert "secret@db" not in str(error)


def test_disabled_knowledge_preserves_minimal_chat_configuration() -> None:
    settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url="https://chat.example.test/v1",
        openai_api_key=SecretStr("chat-secret"),
        openai_model="chat-model",
        knowledge_enabled=False,
        _env_file=None,  # type: ignore[call-arg]
    )

    assert settings.knowledge_enabled is False
