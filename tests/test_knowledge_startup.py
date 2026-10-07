from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from pymilvus import MilvusException  # type: ignore[import-untyped]

from agent_api.core.config import EmbeddingProvider, ModelProvider, Settings
from agent_api.core.lifespan import create_lifespan
from agent_api.knowledge.infrastructure.milvus.wiki_index import build_wiki_milvus_schema
from agent_api.knowledge.infrastructure.startup import (
    KnowledgeStartup,
    StartupCheck,
    StartupCheckError,
    WikiMilvusStartupCheck,
    build_milvus_schema,
    create_knowledge_startup,
    validate_milvus_description,
)
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from tests.fakes import FakeStreamAgent


class RecordingCheck:
    def __init__(self, name: str, events: list[str], *, failure: Exception | None = None) -> None:
        self.name = name
        self.events = events
        self.failure = failure

    async def check(self) -> None:
        self.events.append(f"check:{self.name}")
        if self.failure is not None:
            raise self.failure

    async def close(self) -> None:
        self.events.append(f"close:{self.name}")


def enabled_settings(tmp_path: Path) -> Settings:
    return Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url="https://chat.example.test/v1",
        openai_api_key=SecretStr("chat-secret"),
        openai_model="chat-model",
        knowledge_enabled=True,
        database_url=SecretStr("postgresql+asyncpg://agent:secret@db:5432/agent"),
        redis_url=SecretStr("redis://redis:6379/0"),
        storage_root=tmp_path / "uploads",
        milvus_uri="http://milvus:19530",
        embedding_provider=EmbeddingProvider.OPENAI,
        embedding_base_url="https://embedding.example.test/v1",
        embedding_api_key=SecretStr("embedding-secret"),
        embedding_model="embedding-model",
        embedding_dimension=1024,
        wiki_index_enabled=True,
        _env_file=None,  # type: ignore[call-arg]
    )


def startup(tmp_path: Path, checks: Sequence[StartupCheck]) -> KnowledgeStartup:
    return KnowledgeStartup(LocalFileStorage(tmp_path / "uploads"), checks)


async def test_startup_checks_storage_and_all_external_dependencies(tmp_path: Path) -> None:
    events: list[str] = []
    checks = [RecordingCheck("database", events), RecordingCheck("redis", events)]
    manager = startup(tmp_path, checks)

    await manager.start()
    await manager.close()

    assert (tmp_path / "uploads").is_dir()
    assert events == ["check:database", "check:redis", "close:redis", "close:database"]


async def test_startup_failure_closes_initialized_checks_and_redacts_cause(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    secret = "postgresql+asyncpg://agent:top-secret@db:5432/agent"
    checks = [
        RecordingCheck("database", events),
        RecordingCheck("redis", events, failure=RuntimeError(secret)),
    ]
    manager = startup(tmp_path, checks)

    with pytest.raises(StartupCheckError) as captured:
        await manager.start()

    assert captured.value.check_name == "redis"
    assert secret not in str(captured.value)
    assert events == ["check:database", "check:redis", "close:database"]


async def test_lifespan_starts_and_closes_enabled_knowledge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = enabled_settings(tmp_path)
    events: list[str] = []
    manager = startup(tmp_path, [RecordingCheck("database", events)])

    class FakeMilvusClient:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    milvus = FakeMilvusClient()
    monkeypatch.setattr("agent_api.core.lifespan.MilvusClient", lambda **_: milvus)
    lifespan = create_lifespan(
        settings_factory=lambda: settings,
        agent_factory=lambda _: FakeStreamAgent(),
        knowledge_startup_factory=lambda _: manager,
    )
    app = FastAPI(lifespan=lifespan)

    async with lifespan(app):
        assert app.state.knowledge_startup is manager
        assert events == ["check:database"]

    assert events == ["check:database", "close:database"]
    assert milvus.close_calls == 1
    assert not hasattr(app.state, "knowledge_startup")


async def test_lifespan_does_not_construct_knowledge_runtime_when_disabled() -> None:
    settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url="https://chat.example.test/v1",
        openai_api_key=SecretStr("chat-secret"),
        openai_model="chat-model",
        knowledge_enabled=False,
        _env_file=None,  # type: ignore[call-arg]
    )

    def unexpected_factory(_: Settings) -> KnowledgeStartup:
        raise AssertionError("disabled knowledge must not create external clients")

    lifespan = create_lifespan(
        settings_factory=lambda: settings,
        agent_factory=lambda _: FakeStreamAgent(),
        knowledge_startup_factory=unexpected_factory,
    )
    app = FastAPI(lifespan=lifespan)

    async with lifespan(app):
        assert not hasattr(app.state, "knowledge_startup")


def test_milvus_schema_contains_dense_bm25_and_scope_fields() -> None:
    schema = build_milvus_schema(1024).to_dict()
    fields = {field["name"]: field for field in schema["fields"]}

    assert set(fields) == {
        "chunk_id",
        "content",
        "dense_vector",
        "document_version_id",
        "knowledge_base_id",
        "sparse_vector",
    }
    assert fields["dense_vector"]["params"]["dim"] == 1024
    assert schema["functions"][0]["input_field_names"] == ["content"]
    assert schema["functions"][0]["output_field_names"] == ["sparse_vector"]


def test_milvus_validation_rejects_dimension_mismatch() -> None:
    description = build_milvus_schema(768).to_dict()

    with pytest.raises(RuntimeError, match="dimension"):
        validate_milvus_description(description, expected_dimension=1024)


class FakeStartupMilvusClient:
    def __init__(
        self,
        *,
        collections: dict[str, dict[str, object]] | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.collections = dict(collections or {})
        self.aliases = dict(aliases or {})
        self.created_collections: list[str] = []
        self.created_aliases: list[tuple[str, str]] = []
        self.altered_aliases: list[tuple[str, str]] = []
        self.dropped_collections: list[str] = []
        self.close_calls = 0

    def has_collection(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_collection(
        self,
        collection_name: str,
        schema: object,
        index_params: object,
    ) -> None:
        del index_params
        self.collections[collection_name] = schema.to_dict()  # type: ignore[attr-defined]
        self.created_collections.append(collection_name)

    def describe_collection(self, collection_name: str) -> dict[str, object]:
        return self.collections[collection_name]

    def describe_alias(self, alias: str) -> dict[str, str]:
        if alias not in self.aliases:
            raise MilvusException(message="alias not found")
        return {"alias": alias, "collection_name": self.aliases[alias]}

    def create_alias(self, collection_name: str, alias: str) -> None:
        self.aliases[alias] = collection_name
        self.created_aliases.append((alias, collection_name))

    def alter_alias(self, collection_name: str, alias: str) -> None:
        self.aliases[alias] = collection_name
        self.altered_aliases.append((alias, collection_name))

    def drop_collection(self, collection_name: str) -> None:
        self.dropped_collections.append(collection_name)

    def close(self) -> None:
        self.close_calls += 1


async def test_wiki_startup_creates_physical_collection_and_first_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeStartupMilvusClient()
    monkeypatch.setattr(
        "agent_api.knowledge.infrastructure.startup.MilvusClient",
        lambda **_: client,
    )
    manager = create_knowledge_startup(enabled_settings(tmp_path))
    wiki_check = next(check for check in manager.checks if check.name == "wiki_milvus")

    assert isinstance(wiki_check, WikiMilvusStartupCheck)
    await wiki_check.check()

    assert client.created_collections == ["wiki_units_v1"]
    assert client.created_aliases == [("wiki_units", "wiki_units_v1")]
    assert client.altered_aliases == []
    assert client.dropped_collections == []


async def test_wiki_startup_keeps_existing_alias_and_collections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = build_wiki_milvus_schema(1024).to_dict()
    client = FakeStartupMilvusClient(
        collections={"wiki_units_v1": schema, "wiki_units_previous": schema},
        aliases={"wiki_units": "wiki_units_previous"},
    )
    monkeypatch.setattr(
        "agent_api.knowledge.infrastructure.startup.MilvusClient",
        lambda **_: client,
    )
    manager = create_knowledge_startup(enabled_settings(tmp_path))
    wiki_check = next(check for check in manager.checks if check.name == "wiki_milvus")

    await wiki_check.check()

    assert client.aliases == {"wiki_units": "wiki_units_previous"}
    assert client.created_collections == []
    assert client.created_aliases == []
    assert client.altered_aliases == []
    assert client.dropped_collections == []


async def test_wiki_startup_rejects_missing_existing_alias_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = build_wiki_milvus_schema(1024).to_dict()
    client = FakeStartupMilvusClient(
        collections={"wiki_units_v1": schema},
        aliases={"wiki_units": "wiki_units_missing"},
    )
    monkeypatch.setattr(
        "agent_api.knowledge.infrastructure.startup.MilvusClient",
        lambda **_: client,
    )
    manager = create_knowledge_startup(enabled_settings(tmp_path))
    wiki_check = next(check for check in manager.checks if check.name == "wiki_milvus")

    with pytest.raises(RuntimeError, match="target collection"):
        await wiki_check.check()

    assert client.created_aliases == []
    assert client.altered_aliases == []
    assert client.dropped_collections == []


async def test_wiki_startup_rejects_existing_alias_dimension_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_schema = build_wiki_milvus_schema(1024).to_dict()
    incompatible_schema = build_wiki_milvus_schema(768).to_dict()
    client = FakeStartupMilvusClient(
        collections={
            "wiki_units_v1": configured_schema,
            "wiki_units_previous": incompatible_schema,
        },
        aliases={"wiki_units": "wiki_units_previous"},
    )
    monkeypatch.setattr(
        "agent_api.knowledge.infrastructure.startup.MilvusClient",
        lambda **_: client,
    )
    manager = create_knowledge_startup(enabled_settings(tmp_path))
    wiki_check = next(check for check in manager.checks if check.name == "wiki_milvus")

    with pytest.raises(RuntimeError, match="dimension"):
        await wiki_check.check()

    assert client.altered_aliases == []
    assert client.dropped_collections == []
