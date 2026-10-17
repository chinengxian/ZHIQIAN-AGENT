import importlib.util
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint

from agent_api.knowledge.domain.statuses import (
    DocumentStatus,
    IngestionJobStatus,
    VersionStatus,
)
from agent_api.knowledge.infrastructure.database.models import Base
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.startup import EXPECTED_DATABASE_REVISION


def _load_migration(filename: str, module_name: str) -> ModuleType:
    """按文件加载迁移，避免数字开头的模块名无法常规导入。"""
    path = Path.cwd() / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_initial_migration() -> ModuleType:
    return _load_migration(
        "20260920_0001_create_knowledge_schema.py",
        "knowledge_schema_migration",
    )


def _load_independent_index_migration() -> ModuleType:
    return _load_migration(
        "20261007_0005_wiki_independent_index.py",
        "wiki_independent_index_migration",
    )


class MigrationOperationRecorder:
    """记录迁移调用，直接验证迁移文件而不连接用户数据库。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, operation: str) -> Callable[..., None]:
        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((operation, args, kwargs))

        return record


def test_knowledge_schema_contains_all_source_of_truth_tables() -> None:
    assert set(Base.metadata.tables) == {
        "chunks",
        "document_versions",
        "documents",
        "ingestion_jobs",
        "knowledge_bases",
        "outbox_events",
        "workspaces",
        "wiki_claims",
        "wiki_claim_sources",
        "wiki_configs",
        "wiki_jobs",
        "wiki_page_aliases",
        "wiki_pages",
        "wiki_page_versions",
        "wiki_token_usage",
    }


def test_wiki_search_schema_declares_aliases_claim_metadata_and_reverse_indexes() -> None:
    """Wiki 检索结构必须同时支持别名、稳定排序和来源反查。"""

    aliases = Base.metadata.tables["wiki_page_aliases"]
    claims = Base.metadata.tables["wiki_claims"]
    sources = Base.metadata.tables["wiki_claim_sources"]

    assert {"page_id", "alias", "normalized_alias"} <= set(aliases.c.keys())
    assert claims.c.position.nullable is False
    assert claims.c.claim_type.nullable is False
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_wiki_claim_type"
        for constraint in claims.constraints
    )
    claim_type_constraint = next(
        constraint
        for constraint in claims.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_wiki_claim_type"
    )
    assert "summary_section" in str(claim_type_constraint.sqltext)
    assert any(index.name == "ix_wiki_aliases_normalized" for index in aliases.indexes)
    assert any(index.name == "ix_wiki_claim_sources_chunk" for index in sources.indexes)
    assert any(index.name == "ix_wiki_claim_sources_locator" for index in sources.indexes)


def test_wiki_independent_index_schema_declares_version_and_job_lifecycle() -> None:
    """Wiki 独立索引必须持久化版本状态与任务诊断上下文。"""

    versions = Base.metadata.tables["wiki_page_versions"]
    jobs = Base.metadata.tables["wiki_jobs"]

    assert {
        "index_state",
        "embedding_fingerprint",
        "indexed_at",
        "index_error_code",
    } <= set(versions.c.keys())
    assert versions.c.index_state.default is not None
    assert versions.c.index_state.default.arg == "not_required"
    assert versions.c.index_state.server_default is not None
    server_default_sql = str(versions.c.index_state.server_default.arg).strip()
    assert server_default_sql in {"not_required", "'not_required'"}
    assert {"page_version_id", "embedding_fingerprint", "error_reference"} <= set(
        jobs.c.keys()
    )
    page_version_foreign_keys = list(jobs.c.page_version_id.foreign_keys)
    assert len(page_version_foreign_keys) == 1
    assert page_version_foreign_keys[0].target_fullname == "wiki_page_versions.id"
    assert page_version_foreign_keys[0].constraint.name == "fk_wiki_jobs_page_version_id"
    assert page_version_foreign_keys[0].ondelete == "SET NULL"
    index_state_constraint = next(
        constraint
        for constraint in versions.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_wiki_page_version_index_state"
    )
    index_state_sql = str(index_state_constraint.sqltext)
    assert "index_state" in index_state_sql
    assert set(re.findall(r"'([^']+)'", index_state_sql)) == {
        "not_required",
        "pending",
        "indexing",
        "ready",
        "failed",
    }
    assert any(
        index.name == "ix_wiki_jobs_page_version_status"
        and tuple(index.columns.keys()) == ("page_version_id", "status")
        for index in jobs.indexes
    )


def test_wiki_independent_index_migration_upgrade_contract() -> None:
    """迁移升级必须完整创建独立索引字段、约束、外键与索引。"""

    migration = _load_independent_index_migration()
    recorder = MigrationOperationRecorder()
    migration.op = recorder

    migration.upgrade()

    added_columns = {
        (str(args[0]), args[1].name): args[1]
        for operation, args, _ in recorder.calls
        if operation == "add_column"
    }
    assert set(added_columns) == {
        ("wiki_page_versions", "index_state"),
        ("wiki_page_versions", "embedding_fingerprint"),
        ("wiki_page_versions", "indexed_at"),
        ("wiki_page_versions", "index_error_code"),
        ("wiki_jobs", "page_version_id"),
        ("wiki_jobs", "embedding_fingerprint"),
        ("wiki_jobs", "error_reference"),
    }
    index_state = added_columns[("wiki_page_versions", "index_state")]
    assert str(index_state.type).upper() == "VARCHAR(16)"
    assert index_state.nullable is False
    assert index_state.server_default is not None
    assert str(index_state.server_default.arg).strip() in {
        "not_required",
        "'not_required'",
    }

    check_constraints = [
        (args, kwargs)
        for operation, args, kwargs in recorder.calls
        if operation == "create_check_constraint"
    ]
    assert len(check_constraints) == 1
    check_args, check_kwargs = check_constraints[0]
    assert check_args[:2] == (
        "ck_wiki_page_version_index_state",
        "wiki_page_versions",
    )
    assert check_kwargs == {}
    assert set(re.findall(r"'([^']+)'", str(check_args[2]))) == {
        "not_required",
        "pending",
        "indexing",
        "ready",
        "failed",
    }

    foreign_keys = [
        (args, kwargs)
        for operation, args, kwargs in recorder.calls
        if operation == "create_foreign_key"
    ]
    assert foreign_keys == [
        (
            (
                "fk_wiki_jobs_page_version_id",
                "wiki_jobs",
                "wiki_page_versions",
                ["page_version_id"],
                ["id"],
            ),
            {"ondelete": "SET NULL"},
        )
    ]
    indexes = [
        (args, kwargs)
        for operation, args, kwargs in recorder.calls
        if operation == "create_index"
    ]
    assert indexes == [
        (
            (
                "ix_wiki_jobs_page_version_status",
                "wiki_jobs",
                ["page_version_id", "status"],
            ),
            {},
        )
    ]


def test_wiki_independent_index_migration_downgrade_is_metadata_only() -> None:
    """迁移回滚只能逆序删除本次新增元数据，不能删除业务数据。"""

    migration = _load_independent_index_migration()
    recorder = MigrationOperationRecorder()
    migration.op = recorder

    migration.downgrade()

    assert recorder.calls == [
        (
            "drop_index",
            ("ix_wiki_jobs_page_version_status",),
            {"table_name": "wiki_jobs"},
        ),
        ("drop_column", ("wiki_jobs", "error_reference"), {}),
        ("drop_column", ("wiki_jobs", "embedding_fingerprint"), {}),
        (
            "drop_constraint",
            ("fk_wiki_jobs_page_version_id", "wiki_jobs"),
            {"type_": "foreignkey"},
        ),
        ("drop_column", ("wiki_jobs", "page_version_id"), {}),
        (
            "drop_constraint",
            ("ck_wiki_page_version_index_state", "wiki_page_versions"),
            {"type_": "check"},
        ),
        ("drop_column", ("wiki_page_versions", "index_error_code"), {}),
        ("drop_column", ("wiki_page_versions", "indexed_at"), {}),
        ("drop_column", ("wiki_page_versions", "embedding_fingerprint"), {}),
        ("drop_column", ("wiki_page_versions", "index_state"), {}),
    ]
    recorded_operations = " ".join(
        f"{operation} {args!r}" for operation, args, _ in recorder.calls
    ).upper()
    assert all(
        forbidden not in recorded_operations
        for forbidden in ("DELETE", "TRUNCATE", "DROP TABLE")
    )


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

    assert EXPECTED_DATABASE_REVISION == "20261007_0005"
    assert scripts.get_current_head() == EXPECTED_DATABASE_REVISION


def test_default_workspace_migration_binds_a_native_uuid() -> None:
    """真实 PostgreSQL UUID 列不能接收被推断成 VARCHAR 的字符串参数。"""
    migration = _load_initial_migration()

    statement = migration._default_workspace_insert()
    workspace_id = statement._bindparams["workspace_id"]

    assert isinstance(workspace_id.value, UUID)
    assert workspace_id.type.python_type is UUID
