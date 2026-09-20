import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint

from agent_api.knowledge.domain.statuses import (
    DocumentStatus,
    IngestionJobStatus,
    VersionStatus,
)
from agent_api.knowledge.infrastructure.database.models import Base
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.startup import EXPECTED_DATABASE_REVISION


def _load_initial_migration() -> ModuleType:
    """按文件加载首个迁移，避免数字开头的模块名无法常规导入。"""
    path = Path.cwd() / "migrations" / "versions" / "20260920_0001_create_knowledge_schema.py"
    spec = importlib.util.spec_from_file_location("knowledge_schema_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_knowledge_schema_contains_all_source_of_truth_tables() -> None:
    assert set(Base.metadata.tables) == {
        "chunks",
        "document_versions",
        "documents",
        "ingestion_jobs",
        "knowledge_bases",
        "outbox_events",
        "workspaces",
    }


def test_knowledge_schema_declares_required_uniqueness_and_indexes() -> None:
    knowledge_bases = Base.metadata.tables["knowledge_bases"]
    versions = Base.metadata.tables["document_versions"]
    chunks = Base.metadata.tables["chunks"]
    jobs = Base.metadata.tables["ingestion_jobs"]
    outbox = Base.metadata.tables["outbox_events"]

    kb_unique = {
        tuple(constraint.columns.keys())
        for constraint in knowledge_bases.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    version_unique = {
        tuple(constraint.columns.keys())
        for constraint in versions.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    chunk_unique = {
        tuple(constraint.columns.keys())
        for constraint in chunks.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("workspace_id", "name") in kb_unique
    assert ("document_id", "version_no") in version_unique
    assert ("document_version_id", "ordinal", "kind") in chunk_unique
    assert jobs.c.idempotency_key.unique is True
    assert outbox.c.event_key.unique is True
    assert any(
        index.name == "ix_documents_kb_status"
        for index in Base.metadata.tables["documents"].indexes
    )
    assert any(index.name == "ix_chunks_version_parent" for index in chunks.indexes)


def test_domain_statuses_cover_version_switch_and_recovery_states() -> None:
    assert {status.value for status in DocumentStatus} == {
        "pending",
        "processing",
        "ready",
        "failed",
        "deleting",
        "deleted",
    }
    assert {status.value for status in VersionStatus} == {
        "pending",
        "parsing",
        "chunking",
        "embedding",
        "indexing",
        "ready",
        "failed",
    }
    assert {status.value for status in IngestionJobStatus} == {
        "pending",
        "running",
        "retry_wait",
        "succeeded",
        "failed",
    }


async def test_database_runtime_exposes_async_sessions_without_leaking_url() -> None:
    secret_url = "postgresql+asyncpg://agent:database-secret@db:5432/agent"
    runtime = DatabaseRuntime(secret_url)

    assert runtime.session_factory.class_.__name__ == "AsyncSession"
    assert "database-secret" not in repr(runtime)

    await runtime.close()


def test_alembic_head_matches_startup_revision() -> None:
    config = Config(str(Path.cwd() / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_current_head() == EXPECTED_DATABASE_REVISION


def test_default_workspace_migration_binds_a_native_uuid() -> None:
    """真实 PostgreSQL UUID 列不能接收被推断成 VARCHAR 的字符串参数。"""
    migration = _load_initial_migration()

    statement = migration._default_workspace_insert()
    workspace_id = statement._bindparams["workspace_id"]

    assert isinstance(workspace_id.value, UUID)
    assert workspace_id.type.python_type is UUID
