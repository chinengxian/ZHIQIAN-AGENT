from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from agent_api.core.config import Settings
from agent_api.knowledge.infrastructure.milvus import WikiIndexedUnit

_INDEXABLE_CLAIM_TYPES = frozenset({"summary_section", "summary_fact", "topic_fact"})
_EXECUTABLE_JOB_STATUSES = frozenset({"pending", "retry_wait"})


class WikiPageLike(Protocol):
    """索引服务所需的最小页面字段。"""

    id: UUID
    knowledge_base_id: UUID
    title: str
    page_type: str
    current_version_id: UUID | None


class WikiPageVersionLike(Protocol):
    """索引状态机所需的最小版本字段。"""

    id: UUID
    page_id: UUID
    origin: str
    review_state: str
    index_state: str
    embedding_fingerprint: str | None
    indexed_at: datetime | None
    index_error_code: str | None


class WikiClaimLike(Protocol):
    """构造可检索知识单元所需的 Claim 字段。"""

    id: UUID
    text: str
    claim_type: str
    trust_state: str
    position: int


class WikiIndexJobLike(Protocol):
    """索引状态机所需的最小任务字段。"""

    id: UUID
    page_version_id: UUID | None
    embedding_fingerprint: str | None
    job_type: str
    status: str
    stage: str
    progress: int
    attempt_count: int
    max_attempts: int
    heartbeat_at: datetime | None
    error_code: str | None
    error_reference: str | None


@dataclass(frozen=True, slots=True)
class WikiSourceSnapshot:
    """用于写后复核的 Claim 来源与 Chunk 不可变身份快照。"""

    source_id: UUID
    claim_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID | None
    chunk_kind: str
    chunk_ordinal: int
    content_sha256: str
    heading_path: Sequence[str]
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True, slots=True)
class WikiIndexProjection:
    """数据库批量读取后的索引投影；来源路径由 Claim 来源与 Chunk 共同解析。"""

    aliases: Sequence[str]
    claims: Sequence[WikiClaimLike]
    source_paths: Mapping[UUID, Sequence[str]]
    section_titles: Mapping[UUID, str]
    sources: Sequence[WikiSourceSnapshot]


class WikiIndexRepository(Protocol):
    """隔离数据库锁与事务细节，供 Worker 适配 ORM。"""

    async def lock_job_and_version(
        self,
        job_id: UUID,
    ) -> tuple[WikiIndexJobLike, WikiPageLike, WikiPageVersionLike] | None: ...

    async def save_state(
        self,
        job: WikiIndexJobLike,
        version: WikiPageVersionLike,
    ) -> None: ...

    async def load_aliases_claims_and_sources(
        self,
        *,
        page_id: UUID,
        page_version_id: UUID,
    ) -> WikiIndexProjection: ...

    async def relock_and_reload_projection(
        self,
        *,
        job_id: UUID,
        page_version_id: UUID,
    ) -> tuple[
        WikiIndexJobLike,
        WikiPageLike,
        WikiPageVersionLike,
        WikiIndexProjection,
    ]:
        """在重新锁定的同一事务中批量读取 fresh Claim、来源与 Chunk。"""

        ...


class WikiDocumentEmbedder(Protocol):
    """Wiki 索引只依赖批量文本向量接口。"""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class WikiVectorIndex(Protocol):
    """Wiki 独立索引所需的最小写入与校验接口。"""

    async def replace_page_version(
        self,
        page_version_id: UUID,
        units: Sequence[WikiIndexedUnit],
    ) -> None: ...

    async def count_page_version(self, page_version_id: UUID) -> int: ...

    async def delete_page_version(self, page_version_id: UUID) -> None: ...


class _IndexCountMismatch(RuntimeError):
    """写入后的知识单元数量不符合预期。"""


class _EmbeddingCountMismatch(RuntimeError):
    """Embedding 返回数量与输入文本不一致。"""


def _clean(value: str) -> str:
    """清理首尾与换行空白，同时保持正文内部文字不变。"""

    return value.strip()


def build_unit_content(
    *,
    page_title: str,
    page_type: str,
    aliases: Sequence[str],
    section_title: str | None,
    claim_text: str,
    source_paths: Sequence[str],
) -> str:
    """按固定字段顺序生成可复现的 Wiki 知识单元正文。"""

    clean_aliases = tuple(cleaned for value in aliases if (cleaned := _clean(value)))
    clean_paths = tuple(cleaned for value in source_paths if (cleaned := _clean(value)))
    clean_section = _clean(section_title) if section_title else ""
    parts = (
        _clean(page_title),
        f"页面类型：{_clean(page_type)}" if _clean(page_type) else "",
        f"主题别名：{'、'.join(clean_aliases)}" if clean_aliases else "",
        f"段落标题：{clean_section}" if clean_section else "",
        _clean(claim_text),
        f"来源路径：{'；'.join(clean_paths)}" if clean_paths else "",
    )
    return "\n".join(part for part in parts if part)


def build_index_units(
    *,
    page: WikiPageLike,
    version: WikiPageVersionLike,
    aliases: Sequence[str],
    claims: Sequence[WikiClaimLike],
    source_paths: Mapping[UUID, Sequence[str]],
    section_titles: Mapping[UUID, str] | None = None,
) -> list[WikiIndexedUnit]:
    """只把已验证的受支持 Claim 投影为确定性索引单元。"""

    titles = section_titles or {}
    trusted_claims = _trusted_claims(claims)
    return [
        WikiIndexedUnit(
            unit_id=claim.id,
            knowledge_base_id=page.knowledge_base_id,
            page_id=page.id,
            page_version_id=version.id,
            claim_id=claim.id,
            unit_type=claim.claim_type,
            content=build_unit_content(
                page_title=page.title,
                page_type=page.page_type,
                aliases=aliases,
                section_title=titles.get(claim.id),
                claim_text=claim.text,
                source_paths=source_paths.get(claim.id, ()),
            ),
            dense_vector=(),
        )
        for claim in trusted_claims
    ]


def _trusted_claims(claims: Sequence[WikiClaimLike]) -> list[WikiClaimLike]:
    """按位置和主键返回实际参与索引的可信 Claim。"""

    return sorted(
        (
            claim
            for claim in claims
            if claim.trust_state == "verified"
            and claim.claim_type in _INDEXABLE_CLAIM_TYPES
        ),
        key=lambda claim: (claim.position, str(claim.id)),
    )


def _projection_signature(
    projection: WikiIndexProjection,
    units: Sequence[WikiIndexedUnit],
) -> str:
    """哈希完整可信投影，不把 Claim 正文写入任务、日志或签名载荷。"""

    claims = _trusted_claims(projection.claims)
    trusted_ids = {claim.id for claim in claims}
    sources = sorted(
        (source for source in projection.sources if source.claim_id in trusted_ids),
        key=lambda source: (
            str(source.claim_id),
            str(source.source_id),
            str(source.document_id),
            str(source.document_version_id),
            str(source.chunk_id or ""),
            source.chunk_kind,
            source.chunk_ordinal,
            source.content_sha256,
            tuple(source.heading_path),
            source.page_start if source.page_start is not None else -1,
            source.page_end if source.page_end is not None else -1,
        ),
    )
    payload = {
        "aliases": [_clean(alias) for alias in projection.aliases if _clean(alias)],
        "claims": [
            {
                "id": str(claim.id),
                "type": claim.claim_type,
                "trust": claim.trust_state,
                "position": claim.position,
                "text_sha256": sha256(_clean(claim.text).encode("utf-8")).hexdigest(),
                "section_title": _clean(projection.section_titles.get(claim.id, "")),
                "source_paths": [
                    _clean(path)
                    for path in projection.source_paths.get(claim.id, ())
                    if _clean(path)
                ],
            }
            for claim in claims
        ],
        "sources": [
            {
                "source_id": str(source.source_id),
                "claim_id": str(source.claim_id),
                "document_id": str(source.document_id),
                "document_version_id": str(source.document_version_id),
                "chunk_id": str(source.chunk_id) if source.chunk_id is not None else None,
                "chunk_kind": source.chunk_kind,
                "chunk_ordinal": source.chunk_ordinal,
                "content_sha256": source.content_sha256,
                "heading_path": [_clean(value) for value in source.heading_path],
                "page_start": source.page_start,
                "page_end": source.page_end,
            }
            for source in sources
        ],
        "units": [
            {
                "content_sha256": sha256(unit.content.encode("utf-8")).hexdigest(),
                "unit_id": str(unit.unit_id),
                "unit_type": unit.unit_type,
            }
            for unit in units
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _stable_error_reference(job_id: UUID, stage: str, error: BaseException) -> str:
    """生成不含异常文本的稳定排障引用。"""

    payload = f"{job_id}:{stage}:{type(error).__module__}.{type(error).__qualname__}"
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


class WikiIndexService:
    """驱动 Wiki 当前可信版本从待索引到可检索的状态机。"""

    def __init__(
        self,
        *,
        repository: WikiIndexRepository,
        embedder: WikiDocumentEmbedder,
        index: WikiVectorIndex,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._index = index
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self, job_id: UUID) -> None:
        """执行一次可幂等重试的页面版本索引任务。"""

        locked = await self._repository.lock_job_and_version(job_id)
        if locked is None:
            return
        job, page, version = locked
        if not self._is_executable(job, version):
            return
        if page.current_version_id != version.id:
            await self._mark_superseded(job, version)
            return
        if version.origin == "user" or version.review_state != "published":
            await self._mark_not_required(job, version)
            return

        job.status = "running"
        job.stage = "loading"
        job.progress = 10
        job.attempt_count += 1
        job.heartbeat_at = self._now()
        job.error_code = None
        job.error_reference = None
        version.index_state = "indexing"
        version.index_error_code = None
        await self._repository.save_state(job, version)

        try:
            projection = await self._repository.load_aliases_claims_and_sources(
                page_id=page.id,
                page_version_id=version.id,
            )
            units = build_index_units(
                page=page,
                version=version,
                aliases=projection.aliases,
                claims=projection.claims,
                source_paths=projection.source_paths,
                section_titles=projection.section_titles,
            )
            if not units:
                await self._mark_no_indexable_units(job, version)
                return

            await self._advance(job, version, stage="embedding", progress=35)
            vectors = await self._embedder.embed_documents([unit.content for unit in units])
            if len(vectors) != len(units):
                raise _EmbeddingCountMismatch
            indexed_units = [
                replace(unit, dense_vector=tuple(float(value) for value in vector))
                for unit, vector in zip(units, vectors, strict=True)
            ]

            await self._advance(job, version, stage="writing", progress=70)
            await self._index.replace_page_version(version.id, indexed_units)

            await self._advance(job, version, stage="verifying", progress=90)
            written_count = await self._index.count_page_version(version.id)
            if written_count != len(indexed_units):
                raise _IndexCountMismatch

            original_signature = _projection_signature(projection, units)
            job, page, version, fresh_projection = (
                await self._repository.relock_and_reload_projection(
                    job_id=job_id,
                    page_version_id=version.id,
                )
            )
            fresh_units = build_index_units(
                page=page,
                version=version,
                aliases=fresh_projection.aliases,
                claims=fresh_projection.claims,
                source_paths=fresh_projection.source_paths,
                section_titles=fresh_projection.section_titles,
            )
            projection_changed = (
                _projection_signature(fresh_projection, fresh_units) != original_signature
            )
            target_changed = (
                page.current_version_id != version.id
                or job.page_version_id != version.id
            )
            if target_changed or projection_changed:
                await self._index.delete_page_version(version.id)
                await self._mark_superseded(job, version)
                return

            version.index_state = "ready"
            version.embedding_fingerprint = job.embedding_fingerprint
            version.indexed_at = self._now()
            version.index_error_code = None
            job.status = "succeeded"
            job.stage = "completed"
            job.progress = 100
            job.heartbeat_at = self._now()
            job.error_code = None
            job.error_reference = None
            await self._repository.save_state(job, version)
        except Exception as error:
            await self._mark_failed(job, version, error)

    @staticmethod
    def _is_executable(job: WikiIndexJobLike, version: WikiPageVersionLike) -> bool:
        return (
            job.job_type == "page_index"
            and job.page_version_id == version.id
            and job.status in _EXECUTABLE_JOB_STATUSES
            and version.index_state in {"pending", "failed"}
        )

    async def _advance(
        self,
        job: WikiIndexJobLike,
        version: WikiPageVersionLike,
        *,
        stage: str,
        progress: int,
    ) -> None:
        job.stage = stage
        job.progress = progress
        job.heartbeat_at = self._now()
        await self._repository.save_state(job, version)

    async def _mark_not_required(
        self,
        job: WikiIndexJobLike,
        version: WikiPageVersionLike,
    ) -> None:
        version.index_state = "not_required"
        version.embedding_fingerprint = None
        version.indexed_at = None
        version.index_error_code = None
        job.status = "succeeded"
        job.stage = "not_required"
        job.progress = 100
        job.heartbeat_at = self._now()
        job.error_code = None
        job.error_reference = None
        await self._repository.save_state(job, version)

    async def _mark_no_indexable_units(
        self,
        job: WikiIndexJobLike,
        version: WikiPageVersionLike,
    ) -> None:
        version.index_state = "not_required"
        version.embedding_fingerprint = None
        version.indexed_at = None
        version.index_error_code = None
        job.status = "succeeded"
        job.stage = "no_indexable_units"
        job.progress = 100
        job.heartbeat_at = self._now()
        job.error_code = None
        job.error_reference = None
        await self._repository.save_state(job, version)

    async def _mark_superseded(
        self,
        job: WikiIndexJobLike,
        version: WikiPageVersionLike,
    ) -> None:
        version.index_state = "not_required"
        version.embedding_fingerprint = None
        version.indexed_at = None
        version.index_error_code = None
        job.status = "succeeded"
        job.stage = "superseded"
        job.progress = 100
        job.heartbeat_at = self._now()
        job.error_code = None
        job.error_reference = None
        await self._repository.save_state(job, version)

    async def _mark_failed(
        self,
        job: WikiIndexJobLike,
        version: WikiPageVersionLike,
        error: BaseException,
    ) -> None:
        failure_stage = job.stage
        error_code = self._error_code(failure_stage, error)
        version.index_state = "failed"
        version.embedding_fingerprint = None
        version.indexed_at = None
        version.index_error_code = error_code
        job.status = "retry_wait" if job.attempt_count < job.max_attempts else "failed"
        job.stage = "failed"
        job.heartbeat_at = self._now()
        job.error_code = error_code
        job.error_reference = _stable_error_reference(job.id, failure_stage, error)
        await self._repository.save_state(job, version)

    @staticmethod
    def _error_code(stage: str, error: BaseException) -> str:
        if isinstance(error, _IndexCountMismatch):
            return "index_count_mismatch"
        if isinstance(error, _EmbeddingCountMismatch):
            return "embedding_count_mismatch"
        return {
            "loading": "index_loading_failed",
            "embedding": "embedding_failed",
            "writing": "index_write_failed",
            "verifying": "index_verify_failed",
        }.get(stage, "index_failed")


def embedding_fingerprint(settings: Settings) -> str:
    """生成只描述 Embedding 索引兼容性的稳定指纹。"""

    payload = {
        "dimension": settings.embedding_dimension,
        "index_protocol": settings.wiki_index_protocol_version,
        "model": settings.embedding_model,
        "provider": settings.embedding_provider.value,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def relevance_fingerprint(settings: Settings) -> str:
    """生成只描述相关性策略且不包含查询或凭据的稳定指纹。"""

    payload = {
        "embedding_fingerprint": embedding_fingerprint(settings),
        "keyword_coverage_threshold": settings.wiki_keyword_coverage_threshold,
        "profile": settings.wiki_relevance_profile,
        "rerank_model": settings.rerank_model,
        "rerank_provider": settings.rerank_provider.value,
        "rerank_threshold": settings.wiki_rerank_threshold,
        "dense_threshold": settings.wiki_dense_threshold,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
