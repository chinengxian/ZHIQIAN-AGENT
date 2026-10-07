from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from agent_api.core.config import (
    EmbeddingProvider,
    ModelProvider,
    Settings,
    WikiSearchBackend,
)


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
        "embedding_base_url": None,
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


def test_wiki_index_defaults_are_safe_and_legacy_compatible(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path)

    assert settings.wiki_index_enabled is False
    assert settings.wiki_search_backend is WikiSearchBackend.LEGACY
    assert settings.wiki_milvus_collection_alias == "wiki_units"
    assert settings.wiki_milvus_collection_version == "wiki_units_v1"
    assert settings.wiki_index_protocol_version == "wiki-index-v1"
    assert settings.wiki_relevance_profile == "wiki-relevance-v1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wiki_dense_threshold", -0.01),
        ("wiki_dense_threshold", 1.01),
        ("wiki_rerank_threshold", -0.01),
        ("wiki_rerank_threshold", 1.01),
        ("wiki_keyword_coverage_threshold", -0.01),
        ("wiki_keyword_coverage_threshold", 1.01),
    ],
)
def test_wiki_thresholds_must_be_in_closed_unit_interval(
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    with pytest.raises(ValidationError, match=field):
        knowledge_settings(tmp_path, **{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "wiki_milvus_collection_alias",
        "wiki_milvus_collection_version",
        "wiki_index_protocol_version",
        "wiki_relevance_profile",
    ],
)
def test_enabled_wiki_index_rejects_blank_identity_fields(
    tmp_path: Path,
    field: str,
) -> None:
    with pytest.raises(ValidationError) as captured:
        knowledge_settings(tmp_path, wiki_index_enabled=True, **{field: "   "})

    assert any(detail["loc"] == (field,) for detail in captured.value.errors())


@pytest.mark.parametrize("backend", ["shadow", "independent"])
def test_non_legacy_wiki_backend_requires_enabled_index(
    tmp_path: Path,
    backend: str,
) -> None:
    with pytest.raises(ValidationError, match="wiki_index_enabled"):
        knowledge_settings(
            tmp_path,
            wiki_search_backend=backend,
            wiki_index_enabled=False,
        )


def test_legacy_wiki_backend_allows_disabled_index(tmp_path: Path) -> None:
    settings = knowledge_settings(
        tmp_path,
        wiki_search_backend="legacy",
        wiki_index_enabled=False,
    )

    assert settings.wiki_search_backend is WikiSearchBackend.LEGACY


@pytest.mark.parametrize("backend", ["shadow", "independent"])
def test_disabled_knowledge_rejects_non_legacy_backend_without_index(
    backend: str,
) -> None:
    secret = "pure-chat-secret"

    with pytest.raises(ValidationError) as captured:
        Settings(
            model_provider=ModelProvider.OPENAI,
            openai_base_url="https://chat.example.test/v1",
            openai_api_key=secret,
            openai_model="chat-model",
            knowledge_enabled=False,
            wiki_search_backend=backend,
            wiki_index_enabled=False,
            _env_file=None,  # type: ignore[call-arg]
        )

    error = captured.value
    assert any(detail["loc"] == ("wiki_index_enabled",) for detail in error.errors())
    assert secret not in str(error)
    assert secret not in repr(error)


def test_embedding_provider_defaults_to_dashscope() -> None:
    settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url="https://chat.example.test/v1",
        openai_api_key=SecretStr("chat-secret"),
        openai_model="chat-model",
        knowledge_enabled=False,
        _env_file=None,  # type: ignore[call-arg]
    )

    assert settings.embedding_provider is EmbeddingProvider.DASHSCOPE


def test_dashscope_embedding_does_not_require_base_url(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path)

    assert settings.embedding_provider is EmbeddingProvider.DASHSCOPE
    assert settings.embedding_base_url is None


def test_openai_embedding_requires_base_url(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="embedding_base_url"):
        knowledge_settings(tmp_path, embedding_provider="openai")


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
    assert settings.wiki_search_backend is WikiSearchBackend.LEGACY
    assert settings.wiki_index_enabled is False
