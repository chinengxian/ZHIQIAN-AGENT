from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from agent_api.knowledge.wiki.indexing import (
    WikiIndexProjection,
    WikiIndexService,
    WikiSourceSnapshot,
    build_index_units,
    build_unit_content,
    embedding_fingerprint,
    relevance_fingerprint,
)
from tests.test_knowledge_config import knowledge_settings


def test_embedding_fingerprint_is_stable_for_same_configuration(tmp_path: Path) -> None:
    first = knowledge_settings(tmp_path)
    second = knowledge_settings(tmp_path)

    assert embedding_fingerprint(first) == embedding_fingerprint(second)
    assert len(embedding_fingerprint(first)) == 64


def test_embedding_fingerprint_distinguishes_none_from_empty_model(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path)
    missing_model = settings.model_copy(update={"embedding_model": None})
    empty_model = settings.model_copy(update={"embedding_model": ""})

    assert embedding_fingerprint(missing_model) != embedding_fingerprint(empty_model)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("embedding_provider", "openai"),
        ("embedding_model", "text-embedding-other"),
        ("embedding_dimension", 2048),
        ("wiki_index_protocol_version", "wiki-index-v2"),
    ],
)
def test_embedding_fingerprint_changes_with_index_compatibility_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    baseline = knowledge_settings(tmp_path)
    overrides: dict[str, object] = {field: value}
    if field == "embedding_provider":
        overrides["embedding_base_url"] = "https://embedding.example.test/v1"
    changed = knowledge_settings(tmp_path, **overrides)

    assert embedding_fingerprint(baseline) != embedding_fingerprint(changed)


def test_relevance_fingerprint_is_stable_for_same_configuration(tmp_path: Path) -> None:
    first = knowledge_settings(tmp_path, rerank_provider="none", rerank_model=None)
    second = knowledge_settings(tmp_path, rerank_provider="none", rerank_model=None)

    assert relevance_fingerprint(first) == relevance_fingerprint(second)
    assert len(relevance_fingerprint(first)) == 64


def test_relevance_fingerprint_distinguishes_none_from_empty_rerank_model(
    tmp_path: Path,
) -> None:
    settings = knowledge_settings(tmp_path)
    missing_model = settings.model_copy(update={"rerank_model": None})
    empty_model = settings.model_copy(update={"rerank_model": ""})

    assert relevance_fingerprint(missing_model) != relevance_fingerprint(empty_model)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("embedding_model", "text-embedding-other"),
        ("rerank_provider", "none"),
        ("rerank_model", "BAAI/bge-reranker-v2.5"),
        ("wiki_dense_threshold", 0.60),
        ("wiki_rerank_threshold", 0.60),
        ("wiki_keyword_coverage_threshold", 0.60),
        ("wiki_relevance_profile", "wiki-relevance-v2"),
    ],
)
def test_relevance_fingerprint_changes_with_relevance_policy_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    baseline = knowledge_settings(
        tmp_path,
        rerank_provider="local_bge",
        rerank_model="BAAI/bge-reranker-v2-m3",
    )
    changed_values: dict[str, object] = {
        "rerank_provider": "local_bge",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
    }
    changed_values[field] = value
    changed = knowledge_settings(tmp_path, **changed_values)

    assert relevance_fingerprint(baseline) != relevance_fingerprint(changed)


def test_build_unit_content_cleans_whitespace_and_has_stable_field_order() -> None:
    content = build_unit_content(
        page_title="  身份认证  ",
        page_type=" summary ",
        aliases=(" 双因素校验\n ", " MFA ", "", "MFA"),
        section_title=" 登录要求 ",
        claim_text="  必须启用 MFA。\n ",
        source_paths=(" 身份规范 / 认证 ", "  ", " 安全制度 / 登录 ", " 身份规范 / 认证 "),
    )

    assert content == (
        "身份认证\n"
        "页面类型：summary\n"
        "主题别名：MFA、双因素校验\n"
        "段落标题：登录要求\n"
        "必须启用 MFA。\n"
        "来源路径：安全制度 / 登录；身份规范 / 认证"
    )


def test_build_units_indexes_only_verified_supported_claims() -> None:
    page_id = uuid4()
    version_id = uuid4()
    first_id = UUID("00000000-0000-0000-0000-000000000002")
    second_id = UUID("00000000-0000-0000-0000-000000000001")
    ignored_manual_id = uuid4()
    ignored_review_id = uuid4()
    claims = (
        _claim(first_id, "必须启用 MFA", "summary_fact", "verified", position=2),
        _claim(second_id, "认证要求概述", "summary_section", "verified", position=1),
        _claim(ignored_manual_id, "人工补充", "manual", "verified", position=0),
        _claim(ignored_review_id, "旧事实", "topic_fact", "needs_review", position=0),
    )

    units = build_index_units(
        page=_page(page_id=page_id, title="身份认证", page_type="summary"),
        version=_version(version_id=version_id, page_id=page_id),
        aliases=("MFA", "双因素校验"),
        claims=claims,
        source_paths={
            first_id: ("身份规范 / 认证",),
            second_id: ("安全制度 / 登录",),
        },
        section_titles={second_id: "概述"},
    )

    assert [unit.unit_id for unit in units] == [second_id, first_id]
    assert [unit.unit_type for unit in units] == ["summary_section", "summary_fact"]
    assert all(unit.unit_id == unit.claim_id for unit in units)
    assert all(unit.page_id == page_id for unit in units)
    assert all(unit.page_version_id == version_id for unit in units)
    combined = "\n".join(unit.content for unit in units)
    assert "身份认证" in combined
    assert "MFA、双因素校验" in combined
    assert "身份规范 / 认证" in combined
    assert "人工补充" not in combined
    assert "旧事实" not in combined


def test_build_units_returns_empty_without_trusted_indexable_claims() -> None:
    page_id = uuid4()
    version_id = uuid4()

    units = build_index_units(
        page=_page(page_id=page_id),
        version=_version(version_id=version_id, page_id=page_id),
        aliases=(),
        claims=(
            _claim(uuid4(), "人工内容", "manual", "verified"),
            _claim(uuid4(), "待复核内容", "summary_fact", "needs_review"),
        ),
        source_paths={},
    )

    assert units == []


@pytest.mark.asyncio
async def test_index_service_moves_pending_to_ready_in_strict_stage_order() -> None:
    fixture = _service_fixture()

    await fixture.service.run(fixture.job.id)

    assert fixture.repository.saved_states == [
        ("running", "loading", 10, "indexing"),
        ("running", "embedding", 35, "indexing"),
        ("running", "writing", 70, "indexing"),
        ("running", "verifying", 90, "indexing"),
        ("succeeded", "completed", 100, "ready"),
    ]
    assert fixture.calls == [
        "lock",
        "save:loading",
        "load_projection",
        "save:embedding",
        "embed",
        "save:writing",
        "replace",
        "save:verifying",
        "count",
        "relock_reload",
        "save:completed",
    ]
    assert fixture.version.embedding_fingerprint == fixture.job.embedding_fingerprint
    assert fixture.version.indexed_at == fixture.now
    assert fixture.version.index_error_code is None
    assert fixture.index.units[0].dense_vector == (0.1, 0.2)


@pytest.mark.asyncio
async def test_index_service_fails_when_written_count_does_not_match() -> None:
    fixture = _service_fixture(index_count=0, attempt_count=0, max_attempts=3)

    await fixture.service.run(fixture.job.id)

    assert fixture.version.index_state == "failed"
    assert fixture.version.index_error_code == "index_count_mismatch"
    assert fixture.job.status == "retry_wait"
    assert fixture.job.error_code == "index_count_mismatch"
    assert fixture.job.error_reference
    assert "必须启用 MFA" not in fixture.job.error_reference
    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.calls[-1] == "save:failed"


@pytest.mark.asyncio
async def test_index_service_cleans_write_when_relock_raises() -> None:
    fixture = _service_fixture(relock_error=RuntimeError("内部数据库地址与正文"))

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.job.status == "retry_wait"
    assert fixture.job.error_code == "index_finalize_failed"
    assert "内部数据库地址" not in fixture.job.error_reference


@pytest.mark.asyncio
async def test_index_service_cleans_write_when_final_save_fails() -> None:
    fixture = _service_fixture(fail_save_stage_once="completed")

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.version.index_state == "failed"
    assert fixture.job.status == "retry_wait"
    assert fixture.job.error_code == "index_finalize_failed"


@pytest.mark.asyncio
async def test_index_service_preserves_original_failure_when_cleanup_fails() -> None:
    cleanup_message = "清理失败：内部 Milvus 地址"
    fixture = _service_fixture(
        index_count=0,
        delete_error=RuntimeError(cleanup_message),
    )

    await fixture.service.run(fixture.job.id)

    assert fixture.job.status == "retry_wait"
    assert fixture.job.stage == "cleanup_pending"
    assert fixture.job.error_code == "index_cleanup_failed"
    assert fixture.version.index_state == "failed"
    assert fixture.version.index_error_code == "index_cleanup_failed"
    assert cleanup_message not in fixture.job.error_reference


@pytest.mark.asyncio
async def test_index_service_retries_cleanup_before_any_reindex_work() -> None:
    fixture = _service_fixture(delete_error=RuntimeError("暂时无法删除"))
    projection = fixture.repository.projection
    fixture.repository.fresh_projection = WikiIndexProjection(
        aliases=("已变化别名",),
        claims=projection.claims,
        source_paths=projection.source_paths,
        section_titles=projection.section_titles,
        sources=projection.sources,
    )

    await fixture.service.run(fixture.job.id)
    assert fixture.job.stage == "cleanup_pending"
    assert fixture.job.status == "retry_wait"
    assert fixture.version.index_state == "failed"
    first_embed_count = fixture.calls.count("embed")
    first_replace_count = fixture.calls.count("replace")

    fixture.index.delete_error = None
    fixture.page.current_version_id = uuid4()
    await fixture.service.run(fixture.job.id)

    assert fixture.calls.count("embed") == first_embed_count
    assert fixture.calls.count("replace") == first_replace_count
    assert fixture.calls.count("delete") == 2
    assert fixture.job.status == "succeeded"
    assert fixture.job.stage == "superseded"
    assert fixture.version.index_state == "not_required"


@pytest.mark.asyncio
async def test_index_service_hard_conflict_delete_failure_is_not_silent_success() -> None:
    fixture = _service_fixture(delete_error=RuntimeError("内部删除异常"))
    fixture.repository.job_change_before_relock = ("embedding_fingerprint", "changed")

    await fixture.service.run(fixture.job.id)

    assert fixture.job.status in {"retry_wait", "failed"}
    assert fixture.job.stage == "cleanup_pending"
    assert fixture.job.error_code == "index_cleanup_failed"
    assert fixture.version.index_state == "failed"


@pytest.mark.asyncio
async def test_index_service_uses_terminal_failure_after_last_attempt() -> None:
    fixture = _service_fixture(index_count=0, attempt_count=2, max_attempts=3)

    await fixture.service.run(fixture.job.id)

    assert fixture.job.attempt_count == 3
    assert fixture.job.status == "failed"
    assert fixture.job.error_code == "index_count_mismatch"


@pytest.mark.asyncio
async def test_index_service_does_not_expose_write_error_text() -> None:
    sensitive_message = "写入失败：必须启用 MFA，连接地址含内部信息"
    fixture = _service_fixture(replace_error=RuntimeError(sensitive_message))

    await fixture.service.run(fixture.job.id)

    assert fixture.job.error_code == "index_write_failed"
    assert fixture.version.index_error_code == "index_write_failed"
    assert sensitive_message not in fixture.job.error_reference


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vectors",
    [
        [[]],
        [[0.1]],
        [[0.1, 0.2, 0.3]],
        [[float("nan"), 0.2]],
        [[float("inf"), 0.2]],
        [[float("-inf"), 0.2]],
    ],
)
async def test_index_service_rejects_invalid_vectors_before_index_write(
    vectors: list[list[float]],
) -> None:
    fixture = _service_fixture(vectors=vectors)

    await fixture.service.run(fixture.job.id)

    assert "replace" not in fixture.calls
    assert fixture.index.units == []
    assert fixture.job.error_code == "embedding_vector_invalid"
    assert fixture.version.index_state == "failed"


@pytest.mark.asyncio
async def test_index_service_deletes_write_when_version_changes_during_run() -> None:
    fixture = _service_fixture()
    fixture.repository.change_current_version_before_relock = True

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.job.status == "succeeded"
    assert fixture.job.stage == "superseded"
    assert fixture.version.index_state != "ready"
    assert fixture.version.embedding_fingerprint is None
    assert fixture.calls[-2:] == ["delete", "save:superseded"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_source",
    [
        {"content_sha256": "b" * 64},
        {"chunk_id": UUID("00000000-0000-0000-0000-000000000099")},
        {"document_version_id": UUID("00000000-0000-0000-0000-000000000098")},
    ],
)
async def test_index_service_deletes_write_when_source_identity_becomes_stale(
    changed_source: dict[str, object],
) -> None:
    fixture = _service_fixture()
    original = fixture.repository.projection.sources[0]
    fresh_source = WikiSourceSnapshot(
        source_id=original.source_id,
        claim_id=original.claim_id,
        document_id=original.document_id,
        document_version_id=cast(
            UUID,
            changed_source.get("document_version_id", original.document_version_id),
        ),
        chunk_id=cast(UUID | None, changed_source.get("chunk_id", original.chunk_id)),
        chunk_kind=original.chunk_kind,
        chunk_ordinal=original.chunk_ordinal,
        content_sha256=str(changed_source.get("content_sha256", original.content_sha256)),
        heading_path=original.heading_path,
        page_start=original.page_start,
        page_end=original.page_end,
    )
    fixture.repository.fresh_projection = WikiIndexProjection(
        aliases=fixture.repository.projection.aliases,
        claims=fixture.repository.projection.claims,
        source_paths=fixture.repository.projection.source_paths,
        section_titles=fixture.repository.projection.section_titles,
        sources=(fresh_source,),
    )

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.job.status == "succeeded"
    assert fixture.job.stage == "superseded"
    assert fixture.version.index_state == "not_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["claim_text", "claim_trust", "claim_position", "alias"],
)
async def test_index_service_deletes_write_when_projection_content_changes(
    change: str,
) -> None:
    fixture = _service_fixture()
    projection = fixture.repository.projection
    original_claim = projection.claims[0]
    fresh_claim = _claim(
        original_claim.id,
        "已修改的事实" if change == "claim_text" else original_claim.text,
        original_claim.claim_type,
        "needs_review" if change == "claim_trust" else original_claim.trust_state,
        position=1 if change == "claim_position" else original_claim.position,
    )
    fixture.repository.fresh_projection = WikiIndexProjection(
        aliases=("双因素认证",) if change == "alias" else projection.aliases,
        claims=(fresh_claim,),
        source_paths=projection.source_paths,
        section_titles=projection.section_titles,
        sources=projection.sources,
    )

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.job.stage == "superseded"
    assert fixture.version.index_state != "ready"


@pytest.mark.asyncio
async def test_index_service_source_snapshot_order_does_not_change_signature() -> None:
    claims = (
        _claim(uuid4(), "第一条事实", "summary_fact", "verified", position=1),
        _claim(uuid4(), "第二条事实", "summary_fact", "verified", position=2),
    )
    fixture = _service_fixture(claims=claims)
    projection = fixture.repository.projection
    fixture.repository.fresh_projection = WikiIndexProjection(
        aliases=projection.aliases,
        claims=projection.claims,
        source_paths=projection.source_paths,
        section_titles=projection.section_titles,
        sources=tuple(reversed(projection.sources)),
    )

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == []
    assert fixture.version.index_state == "ready"


@pytest.mark.asyncio
async def test_index_service_alias_and_source_path_order_do_not_change_signature() -> None:
    fixture = _service_fixture()
    projection = fixture.repository.projection
    claim_id = projection.claims[0].id
    fixture.repository.projection = WikiIndexProjection(
        aliases=("MFA", "双因素认证", "MFA"),
        claims=projection.claims,
        source_paths={claim_id: ("规范 / 登录", "安全制度 / 登录", "规范 / 登录")},
        section_titles=projection.section_titles,
        sources=projection.sources,
    )
    fixture.repository.fresh_projection = WikiIndexProjection(
        aliases=("双因素认证", "MFA"),
        claims=projection.claims,
        source_paths={claim_id: ("安全制度 / 登录", "规范 / 登录")},
        section_titles=projection.section_titles,
        sources=tuple(reversed(projection.sources)),
    )

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == []
    assert fixture.version.index_state == "ready"


@pytest.mark.asyncio
async def test_index_service_recovers_retry_wait_with_indexing_version() -> None:
    fixture = _service_fixture()
    fixture.job.status = "retry_wait"
    fixture.version.index_state = "indexing"

    await fixture.service.run(fixture.job.id)

    assert fixture.version.index_state == "ready"
    assert fixture.calls.count("replace") == 1


@pytest.mark.asyncio
async def test_index_service_does_not_reenter_running_indexing_job() -> None:
    fixture = _service_fixture()
    fixture.job.status = "running"
    fixture.version.index_state = "indexing"

    await fixture.service.run(fixture.job.id)

    assert fixture.calls == ["lock"]
    assert "replace" not in fixture.calls


@pytest.mark.asyncio
async def test_index_service_touches_heartbeat_during_blocking_embedding() -> None:
    fixture = _service_fixture(block_embedding=True, heartbeat_interval=0.01)
    task = asyncio.create_task(fixture.service.run(fixture.job.id))
    await asyncio.wait_for(fixture.embedder.started.wait(), timeout=1)
    await asyncio.wait_for(fixture.repository.heartbeat_touched.wait(), timeout=1)
    fixture.embedder.release.set()
    await asyncio.wait_for(task, timeout=1)

    assert fixture.repository.heartbeat_updates
    assert all(job_id == fixture.job.id for job_id, _ in fixture.repository.heartbeat_updates)
    assert fixture.version.index_state == "ready"


@pytest.mark.asyncio
async def test_index_service_cancellation_stops_embedding_child() -> None:
    fixture = _service_fixture(block_embedding=True, heartbeat_interval=0.01)
    task = asyncio.create_task(fixture.service.run(fixture.job.id))
    await asyncio.wait_for(fixture.embedder.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert fixture.embedder.cancelled.is_set()
    assert "replace" not in fixture.calls
    assert fixture.index.units == []


@pytest.mark.asyncio
async def test_index_service_cancellation_settles_replace_then_cleans_write() -> None:
    fixture = _service_fixture(block_replace=True, heartbeat_interval=0.01)
    task = asyncio.create_task(fixture.service.run(fixture.job.id))
    await asyncio.wait_for(fixture.index.replace_started.wait(), timeout=1)

    task.cancel()
    fixture.index.replace_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert fixture.index.replace_finished.is_set()
    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.index.units == []


@pytest.mark.asyncio
async def test_index_service_cancellation_during_count_cleans_existing_write() -> None:
    fixture = _service_fixture(block_count=True, heartbeat_interval=0.01)
    task = asyncio.create_task(fixture.service.run(fixture.job.id))
    await asyncio.wait_for(fixture.index.count_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert fixture.index.count_cancelled.is_set()
    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.index.units == []


@pytest.mark.asyncio
async def test_index_service_cancellation_persists_cleanup_debt_when_delete_fails() -> None:
    fixture = _service_fixture(
        block_replace=True,
        delete_error=RuntimeError("删除服务内部地址"),
        heartbeat_interval=0.01,
    )
    task = asyncio.create_task(fixture.service.run(fixture.job.id))
    await asyncio.wait_for(fixture.index.replace_started.wait(), timeout=1)

    task.cancel()
    fixture.index.replace_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert fixture.job.stage == "cleanup_pending"
    assert fixture.job.status in {"retry_wait", "failed"}
    assert fixture.job.error_code == "index_cleanup_failed"
    assert fixture.version.index_state == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conflict_field", "conflict_value"),
    [
        ("status", "failed"),
        ("job_type", "wiki_generate"),
        ("embedding_fingerprint", "changed"),
    ],
)
async def test_index_service_cleans_write_and_does_not_succeed_on_relock_conflict(
    conflict_field: str,
    conflict_value: str,
) -> None:
    fixture = _service_fixture()
    fixture.repository.job_change_before_relock = (conflict_field, conflict_value)

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.job.status != "succeeded"
    assert fixture.version.index_state != "ready"


@pytest.mark.asyncio
async def test_index_service_rejects_different_target_version_returned_after_relock() -> None:
    fixture = _service_fixture()
    fixture.repository.returned_version = _version(page_id=fixture.page.id)

    await fixture.service.run(fixture.job.id)

    assert fixture.index.deleted == [fixture.version.id]
    assert fixture.version.index_state == "failed"
    assert fixture.job.error_code == "index_state_conflict"


@pytest.mark.asyncio
async def test_index_service_repeated_idempotent_job_does_not_rewrite_units() -> None:
    fixture = _service_fixture()

    await fixture.service.run(fixture.job.id)
    first_replace_count = fixture.calls.count("replace")
    await fixture.service.run(fixture.job.id)

    assert first_replace_count == 1
    assert fixture.calls.count("replace") == 1
    assert fixture.calls[-1] == "lock"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "review_state"),
    [("user", "published"), ("pipeline", "pending_review")],
)
async def test_index_service_leaves_non_indexable_versions_not_required(
    origin: str,
    review_state: str,
) -> None:
    fixture = _service_fixture(origin=origin, review_state=review_state)

    await fixture.service.run(fixture.job.id)

    assert fixture.version.index_state == "not_required"
    assert fixture.job.status == "succeeded"
    assert fixture.job.stage == "not_required"
    assert "embed" not in fixture.calls
    assert "replace" not in fixture.calls


@pytest.mark.asyncio
async def test_index_service_succeeds_without_indexable_claims() -> None:
    fixture = _service_fixture(
        claims=(_claim(uuid4(), "人工内容", "manual", "verified"),),
        source_paths={},
    )

    await fixture.service.run(fixture.job.id)

    assert fixture.version.index_state == "not_required"
    assert fixture.job.status == "succeeded"
    assert fixture.job.stage == "no_indexable_units"
    assert fixture.job.progress == 100
    assert "embed" not in fixture.calls
    assert "replace" not in fixture.calls


def _claim(
    claim_id: UUID,
    text: str,
    claim_type: str,
    trust_state: str,
    *,
    position: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=claim_id,
        text=text,
        claim_type=claim_type,
        trust_state=trust_state,
        position=position,
    )


def _page(
    *,
    page_id: UUID | None = None,
    title: str = "身份认证",
    page_type: str = "summary",
    current_version_id: UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=page_id or uuid4(),
        knowledge_base_id=uuid4(),
        title=title,
        page_type=page_type,
        current_version_id=current_version_id,
    )


def _version(
    *,
    version_id: UUID | None = None,
    page_id: UUID | None = None,
    origin: str = "pipeline",
    review_state: str = "published",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=version_id or uuid4(),
        page_id=page_id or uuid4(),
        origin=origin,
        review_state=review_state,
        index_state="pending",
        embedding_fingerprint=None,
        indexed_at=None,
        index_error_code=None,
    )


class _FakeRepository:
    def __init__(
        self,
        *,
        calls: list[str],
        job: SimpleNamespace,
        page: SimpleNamespace,
        version: SimpleNamespace,
        projection: WikiIndexProjection,
    ) -> None:
        self.calls = calls
        self.job = job
        self.page = page
        self.version = version
        self.projection = projection
        self.fresh_projection = projection
        self.saved_states: list[tuple[str, str, int, str]] = []
        self.change_current_version_before_relock = False
        self.job_change_before_relock: tuple[str, str] | None = None
        self.relock_error: Exception | None = None
        self.returned_version: Any = version
        self.fail_save_stage_once: str | None = None
        self.heartbeat_updates: list[tuple[UUID, datetime]] = []
        self.heartbeat_touched = asyncio.Event()

    async def lock_job_and_version(self, job_id: UUID) -> tuple[Any, Any, Any] | None:
        self.calls.append("lock")
        if job_id != self.job.id:
            return None
        return self.job, self.page, self.version

    async def save_state(self, job: Any, version: Any) -> None:
        self.calls.append(f"save:{job.stage}")
        if self.fail_save_stage_once == job.stage:
            self.fail_save_stage_once = None
            raise RuntimeError("保存失败，含内部上下文")
        self.saved_states.append((job.status, job.stage, job.progress, version.index_state))

    async def touch_heartbeat(self, job_id: UUID, at: datetime) -> None:
        self.heartbeat_updates.append((job_id, at))
        self.heartbeat_touched.set()

    async def load_aliases_claims_and_sources(
        self,
        *,
        page_id: UUID,
        page_version_id: UUID,
    ) -> WikiIndexProjection:
        self.calls.append("load_projection")
        assert page_id == self.page.id
        assert page_version_id == self.version.id
        return self.projection

    async def relock_and_reload_projection(
        self,
        *,
        job_id: UUID,
        page_version_id: UUID,
    ) -> tuple[Any, Any, Any, WikiIndexProjection]:
        self.calls.append("relock_reload")
        assert job_id == self.job.id
        assert page_version_id == self.version.id
        if self.relock_error is not None:
            raise self.relock_error
        if self.change_current_version_before_relock:
            self.page.current_version_id = uuid4()
        if self.job_change_before_relock is not None:
            field, value = self.job_change_before_relock
            setattr(self.job, field, value)
        return self.job, self.page, self.returned_version, self.fresh_projection


class _FakeEmbedder:
    def __init__(
        self,
        calls: list[str],
        *,
        vectors: list[list[float]] | None = None,
        block: bool = False,
    ) -> None:
        self.calls = calls
        self.vectors = vectors
        self.block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append("embed")
        assert texts
        if self.block:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        if self.vectors is not None:
            return self.vectors
        return [[0.1, 0.2] for _ in texts]


class _FakeIndex:
    def __init__(
        self,
        calls: list[str],
        *,
        count: int,
        replace_error: Exception | None = None,
        delete_error: Exception | None = None,
        block_replace: bool = False,
        block_count: bool = False,
    ) -> None:
        self.calls = calls
        self.count = count
        self.replace_error = replace_error
        self.delete_error = delete_error
        self.block_replace = block_replace
        self.block_count = block_count
        self.units: list[Any] = []
        self.deleted: list[UUID] = []
        self.replace_started = asyncio.Event()
        self.replace_release = asyncio.Event()
        self.replace_finished = asyncio.Event()
        self.count_started = asyncio.Event()
        self.count_cancelled = asyncio.Event()

    async def replace_page_version(
        self,
        page_version_id: UUID,
        units: Sequence[Any],
    ) -> None:
        self.calls.append("replace")
        if self.replace_error is not None:
            raise self.replace_error
        if self.block_replace:
            self.replace_started.set()
            await self.replace_release.wait()
        assert all(unit.page_version_id == page_version_id for unit in units)
        self.units = list(units)
        self.replace_finished.set()

    async def count_page_version(self, page_version_id: UUID) -> int:
        self.calls.append("count")
        if self.block_count:
            self.count_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.count_cancelled.set()
                raise
        return self.count

    async def delete_page_version(self, page_version_id: UUID) -> None:
        self.calls.append("delete")
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(page_version_id)
        self.units = []


@dataclass
class _ServiceFixture:
    service: WikiIndexService
    repository: _FakeRepository
    embedder: _FakeEmbedder
    index: _FakeIndex
    calls: list[str]
    job: SimpleNamespace
    page: SimpleNamespace
    version: SimpleNamespace
    now: datetime


def _service_fixture(
    *,
    index_count: int | None = None,
    attempt_count: int = 0,
    max_attempts: int = 3,
    origin: str = "pipeline",
    review_state: str = "published",
    claims: tuple[SimpleNamespace, ...] | None = None,
    source_paths: dict[UUID, tuple[str, ...]] | None = None,
    replace_error: Exception | None = None,
    delete_error: Exception | None = None,
    vectors: list[list[float]] | None = None,
    block_embedding: bool = False,
    heartbeat_interval: float = 30.0,
    relock_error: Exception | None = None,
    fail_save_stage_once: str | None = None,
    block_replace: bool = False,
    block_count: bool = False,
) -> _ServiceFixture:
    calls: list[str] = []
    version = _version(origin=origin, review_state=review_state)
    page = _page(page_id=version.page_id, current_version_id=version.id)
    job = SimpleNamespace(
        id=uuid4(),
        job_type="page_index",
        page_version_id=version.id,
        embedding_fingerprint="f" * 64,
        status="pending",
        stage="pending",
        progress=0,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        heartbeat_at=None,
        error_code=None,
        error_reference=None,
    )
    claim = _claim(uuid4(), "必须启用 MFA", "summary_fact", "verified")
    actual_claims = claims if claims is not None else (claim,)
    actual_paths = source_paths if source_paths is not None else {claim.id: ("规范 / 登录",)}
    projection = WikiIndexProjection(
        aliases=("MFA",),
        claims=actual_claims,
        source_paths=actual_paths,
        section_titles={},
        sources=tuple(
            WikiSourceSnapshot(
                source_id=uuid4(),
                claim_id=item.id,
                document_id=uuid4(),
                document_version_id=uuid4(),
                chunk_id=uuid4(),
                chunk_kind="child",
                chunk_ordinal=index,
                content_sha256="a" * 64,
                heading_path=("规范", "登录"),
                page_start=1,
                page_end=1,
            )
            for index, item in enumerate(actual_claims)
        ),
    )
    repository = _FakeRepository(
        calls=calls,
        job=job,
        page=page,
        version=version,
        projection=projection,
    )
    repository.relock_error = relock_error
    repository.fail_save_stage_once = fail_save_stage_once
    embedder = _FakeEmbedder(calls, vectors=vectors, block=block_embedding)
    index = _FakeIndex(
        calls,
        count=len(actual_claims) if index_count is None else index_count,
        replace_error=replace_error,
        delete_error=delete_error,
        block_replace=block_replace,
        block_count=block_count,
    )
    now = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)
    service = WikiIndexService(
        repository=repository,
        embedder=embedder,
        index=index,
        embedding_dimension=2,
        heartbeat_interval=heartbeat_interval,
        now=lambda: now,
    )
    return _ServiceFixture(service, repository, embedder, index, calls, job, page, version, now)
