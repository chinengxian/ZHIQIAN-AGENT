from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    OutboxEvent,
    WikiClaim,
    WikiClaimSource,
    WikiConfig,
    WikiJob,
    WikiPage,
    WikiPageAlias,
    WikiPageVersion,
    WikiTokenUsage,
)
from agent_api.knowledge.wiki.generator import WikiGeneration, WikiGenerator
from agent_api.knowledge.wiki.presentation import extract_wiki_presentation


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    """发布阶段已重新绑定到数据库分块的事实。"""

    text: str
    chunks: tuple[Chunk, ...]


@dataclass(frozen=True, slots=True)
class GroundedSection:
    """发布阶段已重新绑定到数据库分块的可读摘要段落。"""

    title: str
    text: str
    chunks: tuple[Chunk, ...]


@dataclass(frozen=True, slots=True)
class GroundedTopic:
    """发布阶段已重新绑定到数据库分块的主题。"""

    name: str
    aliases: tuple[str, ...]
    chunks: tuple[Chunk, ...]


def normalize_wiki_alias(value: str) -> str:
    """统一全半角、大小写、标点和连续空白。"""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^\w]+", " ", normalized).strip()


async def retire_stale_topic_pages(
    session: AsyncSession,
    knowledge_base_id: UUID,
    expected_slugs: set[str],
) -> None:
    """软删除本轮已不满足跨文档条件的主题，并清除其别名。"""

    pages = list(
        await session.scalars(
            select(WikiPage).where(
                WikiPage.knowledge_base_id == knowledge_base_id,
                WikiPage.page_type == "topic",
                WikiPage.deleted_at.is_(None),
            )
        )
    )
    stale = [page for page in pages if page.slug not in expected_slugs]
    if not stale:
        return
    retired_at = datetime.now(UTC)
    for page in stale:
        page.deleted_at = retired_at
    await session.execute(
        delete(WikiPageAlias).where(
            WikiPageAlias.page_id.in_([page.id for page in stale])
        )
    )


def _checkpoint_chunks(value: object, chunks: list[Chunk]) -> tuple[Chunk, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("wiki_generation_invalid_source")
    if any(
        not isinstance(index, int)
        or isinstance(index, bool)
        or index < 1
        or index > len(chunks)
        for index in value
    ):
        raise ValueError("wiki_generation_invalid_source")
    return tuple(chunks[index - 1] for index in dict.fromkeys(value))


def parse_generation_checkpoint(
    data: dict[str, Any],
    chunks: list[Chunk],
    *,
    allow_legacy_sections: bool = False,
) -> tuple[
    tuple[GroundedSection, ...], tuple[GroundedClaim, ...], tuple[GroundedTopic, ...]
]:
    """把持久化模型编号重新校验并映射到当前活动版本分块。"""

    raw_sections = data.get("summary_sections")
    raw_claims = data.get("summary_claims")
    raw_topics = data.get("topics")
    if raw_sections is None and allow_legacy_sections:
        raw_sections = []
    if (
        not isinstance(raw_sections, list)
        or (not allow_legacy_sections and not raw_sections)
        or len(raw_sections) > 3
        or not isinstance(raw_claims, list)
        or not raw_claims
        or not isinstance(raw_topics, list)
    ):
        raise ValueError("wiki_generation_invalid_shape")
    allowed_section_titles = {"概述", "核心要求", "例外与版本变化"}
    section_titles: set[str] = set()
    sections: list[GroundedSection] = []
    for item in raw_sections:
        if not isinstance(item, dict):
            raise ValueError("wiki_generation_invalid_shape")
        title = item.get("title")
        text = item.get("text")
        if not isinstance(title, str) or not isinstance(text, str):
            raise ValueError("wiki_generation_invalid_shape")
        normalized_title = title.strip()
        normalized_text = text.strip()
        if (
            normalized_title not in allowed_section_titles
            or normalized_title in section_titles
            or not normalized_text
            or len(normalized_text) > 2000
        ):
            raise ValueError("wiki_generation_invalid_shape")
        section_titles.add(normalized_title)
        sections.append(
            GroundedSection(
                normalized_title,
                normalized_text,
                _checkpoint_chunks(item.get("source_indices"), chunks),
            )
        )
    claims: list[GroundedClaim] = []
    for item in raw_claims:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("wiki_generation_invalid_shape")
        text = item["text"].strip()
        if not text:
            raise ValueError("wiki_generation_invalid_shape")
        claims.append(
            GroundedClaim(text, _checkpoint_chunks(item.get("source_indices"), chunks))
        )
    topics: list[GroundedTopic] = []
    for item in raw_topics:
        if not isinstance(item, dict):
            raise ValueError("wiki_generation_invalid_shape")
        name = item.get("name")
        aliases = item.get("aliases")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(aliases, list)
            or any(not isinstance(alias, str) for alias in aliases)
        ):
            raise ValueError("wiki_generation_invalid_shape")
        topics.append(
            GroundedTopic(
                name.strip(),
                tuple(dict.fromkeys(alias.strip() for alias in aliases if alias.strip())),
                _checkpoint_chunks(item.get("source_indices"), chunks),
            )
        )
    return tuple(sections), tuple(claims), tuple(topics)


def render_summary_content(title: str, claims: tuple[GroundedClaim, ...]) -> str:
    """只渲染有来源的原子事实和定位信息，不复制原文片段。"""

    lines = [f"# {title}", "", "## 摘要"]
    footnotes: list[str] = []
    for claim_index, claim in enumerate(claims, 1):
        markers: list[str] = []
        for source_index, chunk in enumerate(claim.chunks, 1):
            marker = f"claim-{claim_index}-source-{source_index}"
            markers.append(f"[^{marker}]")
            location = f"原文分块 {chunk.ordinal + 1}"
            if chunk.heading_path:
                location += f"（{' / '.join(chunk.heading_path)}）"
            footnotes.append(f"[^{marker}]: {location}")
        lines.append(f"- {claim.text} {' '.join(markers)}")
    if footnotes:
        lines.extend(["", "## 来源", "", *footnotes])
    return "\n".join(lines)


class WikiProcessor:
    """把活动文档版本生成可恢复的 Wiki 页面。"""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], generator: WikiGenerator
    ) -> None:
        self._sessions = sessions
        self._generator = generator

    @staticmethod
    async def recover_stale(
        sessions: async_sessionmaker[AsyncSession], *, limit: int = 100
    ) -> int:
        """重新投递心跳超时的任务，保留生成检查点和已计费记录。"""

        # 生成可能包含多轮摘要调用，超时阈值需大于 Worker 的 30 分钟硬限。
        stale_before = datetime.now(UTC) - timedelta(minutes=35)
        async with sessions() as session, session.begin():
            jobs = list(
                await session.scalars(
                    select(WikiJob)
                    .where(
                        WikiJob.status == "running",
                        WikiJob.heartbeat_at < stale_before,
                    )
                    .order_by(WikiJob.heartbeat_at)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            for job in jobs:
                session.add(
                    OutboxEvent(
                        event_key=f"wiki-recover:{job.id}:{uuid4()}",
                        event_type=OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED,
                        aggregate_id=job.id,
                        payload={"job_id": str(job.id)},
                    )
                )
            return len(jobs)

    async def run(self, job_id: UUID) -> None:
        context = await self._claim(job_id)
        if context is None:
            return
        title, chunks, reserved, checkpoint = context
        if checkpoint is None:
            try:
                generated = await self._generator.generate(
                    title, [chunk.content for chunk in chunks]
                )
            except Exception:
                await self._fail(job_id)
                return
            await self._settle(job_id, generated, reserved)
        await self._publish(job_id)

    async def _claim(
        self, job_id: UUID
    ) -> tuple[str, list[Chunk], int, dict[str, object] | None] | None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None or job.status not in {"pending", "retry_wait", "running"}:
                return None
            now = datetime.now(UTC)
            if (
                job.status == "retry_wait"
                and job.next_retry_at is not None
                and job.next_retry_at > now
            ):
                return None
            if (
                job.status == "running"
                and job.heartbeat_at is not None
                and job.heartbeat_at > now - timedelta(minutes=35)
            ):
                return None
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is None or not config.enabled:
                job.status = "paused_disabled"
                job.stage = "paused"
                return None
            if job.document_version_id is None:
                job.status = "failed"
                job.error_code = "wiki_missing_source_version"
                return None
            document = await session.scalar(
                select(Document).where(
                    Document.active_version_id == job.document_version_id,
                    Document.knowledge_base_id == job.knowledge_base_id,
                    Document.deleted_at.is_(None),
                    Document.status == "ready",
                )
            )
            if document is None:
                job.status = "superseded"
                job.stage = "source_changed"
                return None
            chunks = list(
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.document_version_id == job.document_version_id,
                        Chunk.kind == "child",
                    )
                    .order_by(Chunk.ordinal)
                )
            )
            if not chunks:
                job.status = "failed"
                job.error_code = "wiki_no_source_chunks"
                return None
            checkpoint = job.payload.get("generation")
            if isinstance(checkpoint, dict):
                job.status = "running"
                job.stage = "publishing"
                job.heartbeat_at = datetime.now(UTC)
                return document.title, chunks, 0, checkpoint
            if job.reserved_tokens:
                config.tokens_reserved = max(0, config.tokens_reserved - job.reserved_tokens)
                job.reserved_tokens = 0
            batches = (len(chunks) + 11) // 12
            calls = batches
            while batches > 1:
                batches = (batches + 11) // 12
                calls += batches
            # 中文输入按字符数保守预留，并为每次模型输出和汇总留出余量。
            estimate = sum(min(len(chunk.content), 1600) for chunk in chunks)
            estimate += 1500 * calls
            job.payload = {**job.payload, "estimated_tokens": estimate}
            if config.tokens_charged + config.tokens_reserved + estimate > config.token_limit:
                job.status = "paused_budget"
                job.stage = "paused"
                job.reserved_tokens = 0
                return None
            config.tokens_reserved += estimate
            job.reserved_tokens = estimate
            job.status = "running"
            job.stage = "generating"
            job.attempt_count += 1
            job.heartbeat_at = datetime.now(UTC)
            return document.title, chunks, estimate, None

    async def _settle(self, job_id: UUID, result: WikiGeneration, reserved: int) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None:
                return
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is None:
                return
            charged = result.measured_tokens if result.measured_tokens is not None else reserved
            call_key = f"wiki-generate:{job.id}"
            existing = await session.scalar(
                select(WikiTokenUsage).where(WikiTokenUsage.call_key == call_key)
            )
            if existing is None:
                session.add(
                    WikiTokenUsage(
                        knowledge_base_id=job.knowledge_base_id,
                        job_id=job.id,
                        call_key=call_key,
                        model=self._generator.model_name,
                        reserved_tokens=reserved,
                        measured_tokens=result.measured_tokens,
                        charged_tokens=charged,
                    )
                )
                config.tokens_charged += charged
            config.tokens_reserved = max(0, config.tokens_reserved - job.reserved_tokens)
            job.reserved_tokens = 0
            job.payload = {
                **job.payload,
                "generation": {
                    "summary_claims": [
                        {
                            "text": claim.text,
                            "source_indices": list(claim.source_indices),
                        }
                        for claim in result.summary_claims
                    ],
                    "topics": [
                        {
                            "name": topic.name,
                            "aliases": list(topic.aliases),
                            "source_indices": list(topic.source_indices),
                        }
                        for topic in result.topics
                    ],
                },
            }
            job.stage = "publishing"
            job.heartbeat_at = datetime.now(UTC)

    async def _fail(self, job_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None:
                return
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is not None:
                config.tokens_reserved = max(0, config.tokens_reserved - job.reserved_tokens)
            job.reserved_tokens = 0
            job.error_code = "wiki_generation_failed"
            if job.attempt_count >= job.max_attempts:
                job.status = "failed"
                job.stage = "failed"
                return
            job.status = "retry_wait"
            job.stage = "retry_wait"
            delay = min(300, 2**job.attempt_count)
            job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            session.add(
                OutboxEvent(
                    event_key=f"wiki-retry:{job.id}:{job.attempt_count}",
                    event_type=OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED,
                    aggregate_id=job.id,
                    payload={"job_id": str(job.id)},
                    available_at=job.next_retry_at,
                )
            )

    async def _publish(self, job_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None or job.status != "running" or job.document_version_id is None:
                return
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is None or not config.enabled:
                job.status = "paused_disabled"
                job.stage = "paused"
                return
            document = await session.scalar(
                select(Document).where(
                    Document.active_version_id == job.document_version_id,
                    Document.knowledge_base_id == job.knowledge_base_id,
                    Document.status == "ready",
                    Document.deleted_at.is_(None),
                )
            )
            if document is None:
                job.status = "superseded"
                job.stage = "source_changed"
                return
            data = job.payload.get("generation")
            if not isinstance(data, dict):
                job.status = "failed"
                job.error_code = "wiki_generation_checkpoint_missing"
                return
            chunks = list(
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.document_version_id == job.document_version_id, Chunk.kind == "child"
                    )
                    .order_by(Chunk.ordinal)
                )
            )
            try:
                _, claims, topics = parse_generation_checkpoint(
                    data,
                    chunks,
                    allow_legacy_sections=job.idempotency_key.endswith(":wiki-v1"),
                )
            except ValueError as error:
                job.status = "failed"
                job.stage = "failed"
                job.error_code = str(error)
                return
            presentation = extract_wiki_presentation(document.title, chunks)
            summary = "\n".join(claim.text for claim in claims)
            body = render_summary_content(presentation.title, claims)
            topic_metadata = [
                {
                    "name": topic.name,
                    "aliases": list(topic.aliases),
                    "source_chunk_ids": [str(chunk.id) for chunk in topic.chunks],
                }
                for topic in topics
            ]
            _, version, created = await self._upsert_page(
                session,
                job.knowledge_base_id,
                slug=f"summary/{document.id}",
                page_type="summary",
                title=presentation.title,
                content=body,
                summary=summary,
                metadata={
                    "document_id": str(document.id),
                    "document_version_id": str(job.document_version_id),
                    "presentation_metadata": [
                        {"key": item.key, "label": item.label, "value": item.value}
                        for item in presentation.metadata
                    ],
                    "topics": topic_metadata,
                },
            )
            for position, grounded_claim in enumerate(claims if created else ()):
                claim = WikiClaim(
                    page_version_id=version.id,
                    claim_key=f"summary-{position + 1}",
                    text=grounded_claim.text,
                    trust_state="verified",
                    position=position,
                    claim_type="summary_fact",
                )
                session.add(claim)
                await session.flush()
                for chunk in grounded_claim.chunks:
                    session.add(
                        WikiClaimSource(
                            claim_id=claim.id,
                            document_id=document.id,
                            document_version_id=job.document_version_id,
                            chunk_id=chunk.id,
                            chunk_kind=chunk.kind,
                            chunk_ordinal=chunk.ordinal,
                            content_sha256=sha256(chunk.content.encode()).hexdigest(),
                            document_title=document.title,
                            heading_path=list(chunk.heading_path),
                            page_start=chunk.page_start,
                            page_end=chunk.page_end,
                        )
                    )
            await self._rebuild_topic_pages(session, job.knowledge_base_id)
            await self._rebuild_index(session, job.knowledge_base_id)
            job.status = "succeeded"
            job.stage = "ready"
            job.progress = 100
            job.heartbeat_at = datetime.now(UTC)

    @staticmethod
    async def _upsert_page(
        session: AsyncSession,
        knowledge_base_id: UUID,
        *,
        slug: str,
        page_type: str,
        title: str,
        content: str,
        summary: str,
        metadata: dict[str, object],
    ) -> tuple[WikiPage, WikiPageVersion, bool]:
        page = await session.scalar(
            select(WikiPage).where(
                WikiPage.knowledge_base_id == knowledge_base_id,
                WikiPage.slug == slug,
            )
        )
        if page is None:
            page = WikiPage(
                knowledge_base_id=knowledge_base_id,
                slug=slug,
                page_type=page_type,
                title=title,
            )
            session.add(page)
            await session.flush()
        page.deleted_at = None
        current = (
            await session.get(WikiPageVersion, page.current_version_id)
            if page.current_version_id
            else None
        )
        if (
            current is not None
            and current.title == title
            and current.content == content
            and current.generation_metadata == metadata
        ):
            return page, current, False
        highest = await session.scalar(
            select(WikiPageVersion.version_no)
            .where(WikiPageVersion.page_id == page.id)
            .order_by(WikiPageVersion.version_no.desc())
            .limit(1)
        )
        pending = current is not None and current.origin in {"user", "revert"}
        if pending:
            old_candidates = list(
                await session.scalars(
                    select(WikiPageVersion).where(
                        WikiPageVersion.page_id == page.id,
                        WikiPageVersion.review_state == "pending_review",
                    )
                )
            )
            for candidate in old_candidates:
                candidate.review_state = "superseded"
        version = WikiPageVersion(
            page_id=page.id,
            version_no=int(highest or 0) + 1,
            title=title,
            content=content,
            summary=summary,
            generation_metadata=metadata,
            origin="pipeline",
            review_state="pending_review" if pending else "published",
            base_published_version_id=page.current_version_id,
        )
        session.add(version)
        await session.flush()
        if not pending:
            page.title = title
            page.current_version_id = version.id
        return page, version, True

    @staticmethod
    async def _rebuild_topic_pages(session: AsyncSession, knowledge_base_id: UUID) -> None:
        summaries = list(
            await session.execute(
                select(WikiPage, WikiPageVersion)
                .join(WikiPageVersion, WikiPage.current_version_id == WikiPageVersion.id)
                .where(
                    WikiPage.knowledge_base_id == knowledge_base_id,
                    WikiPage.page_type == "summary",
                    WikiPage.deleted_at.is_(None),
                )
            )
        )
        by_topic: dict[str, dict[str, Any]] = {}
        identity_by_alias: dict[str, str] = {}
        for page, version in summaries:
            for topic in version.generation_metadata.get("topics", []):
                if not isinstance(topic, dict):
                    continue
                name = topic.get("name")
                aliases = topic.get("aliases")
                source_ids = topic.get("source_chunk_ids")
                if (
                    not isinstance(name, str)
                    or not name.strip()
                    or not isinstance(aliases, list)
                    or not isinstance(source_ids, list)
                ):
                    continue
                normalized_names = [
                    normalize_wiki_alias(value)
                    for value in [name, *aliases]
                    if isinstance(value, str) and normalize_wiki_alias(value)
                ]
                if not normalized_names:
                    continue
                key = next(
                    (
                        identity_by_alias[normalized]
                        for normalized in normalized_names
                        if normalized in identity_by_alias
                    ),
                    normalized_names[0],
                )
                for normalized in normalized_names:
                    identity_by_alias[normalized] = key
                entry = by_topic.setdefault(
                    key,
                    {"name": name.strip(), "aliases": [], "items": []},
                )
                entry["aliases"].extend(
                    alias.strip()
                    for alias in aliases
                    if isinstance(alias, str) and alias.strip()
                )
                valid_source_ids: set[UUID] = set()
                for source_id in source_ids:
                    try:
                        valid_source_ids.add(UUID(str(source_id)))
                    except ValueError:
                        continue
                if valid_source_ids:
                    entry["items"].append((page, version, valid_source_ids))
        expected_topic_slugs: set[str] = set()
        for key, entry in by_topic.items():
            items = entry["items"]
            if len({item[0].id for item in items}) < 2:
                continue
            title = str(entry["name"])[:80]
            slug = f"topic/{sha256(key.encode()).hexdigest()[:16]}"
            lines = [f"# {title}", "", "来自多篇文档的相关内容："]
            selected: list[
                tuple[WikiPage, WikiClaim, list[WikiClaimSource]]
            ] = []
            for page, version, topic_source_ids in items:
                claims = list(
                    await session.scalars(
                        select(WikiClaim)
                        .where(
                            WikiClaim.page_version_id == version.id,
                            WikiClaim.trust_state == "verified",
                        )
                        .order_by(WikiClaim.position, WikiClaim.id)
                    )
                )
                page_claims: list[str] = []
                for source_claim in claims:
                    sources = list(
                        await session.scalars(
                            select(WikiClaimSource).where(
                                WikiClaimSource.claim_id == source_claim.id
                            )
                        )
                    )
                    if not any(source.chunk_id in topic_source_ids for source in sources):
                        continue
                    selected.append((page, source_claim, sources))
                    page_claims.append(f"- {source_claim.text}")
                if page_claims:
                    lines.extend(
                        [
                            "",
                            f"## {page.title}",
                            "",
                            *page_claims,
                            "",
                            f"参见 [[{page.slug}|{page.title}]]",
                        ]
                    )
            if not selected:
                continue
            expected_topic_slugs.add(slug)
            topic_page, topic_version, created = await WikiProcessor._upsert_page(
                session,
                knowledge_base_id,
                slug=slug,
                page_type="topic",
                title=title,
                content="\n".join(lines),
                summary=f"{len({item[0].id for item in items})} 篇文档涉及 {title}",
                metadata={
                    "summary_page_ids": [str(item[0].id) for item in items],
                    "summary_version_ids": [str(item[1].id) for item in items],
                    "aliases": list(dict.fromkeys(entry["aliases"])),
                    "source_chunk_ids": sorted(
                        {str(source_id) for item in items for source_id in item[2]}
                    ),
                },
            )
            await WikiProcessor._replace_aliases(
                session,
                topic_page.id,
                title,
                list(dict.fromkeys(entry["aliases"])),
            )
            if not created:
                continue
            for position, (source_page, source_claim, sources) in enumerate(selected):
                claim = WikiClaim(
                    page_version_id=topic_version.id,
                    claim_key=f"{source_page.id}:{source_claim.claim_key}",
                    text=source_claim.text,
                    trust_state=source_claim.trust_state,
                    reason=source_claim.reason,
                    position=position,
                    claim_type="topic_fact",
                )
                session.add(claim)
                await session.flush()
                for source in sources:
                    session.add(
                        WikiClaimSource(
                            claim_id=claim.id,
                            document_id=source.document_id,
                            document_version_id=source.document_version_id,
                            chunk_id=source.chunk_id,
                            chunk_kind=source.chunk_kind,
                            chunk_ordinal=source.chunk_ordinal,
                            content_sha256=source.content_sha256,
                            document_title=source.document_title,
                            heading_path=list(source.heading_path),
                            page_start=source.page_start,
                            page_end=source.page_end,
                        )
                    )
        await retire_stale_topic_pages(session, knowledge_base_id, expected_topic_slugs)

    @staticmethod
    async def _replace_aliases(
        session: AsyncSession,
        page_id: UUID,
        title: str,
        aliases: list[str],
    ) -> None:
        """以一次事务替换页面别名，避免旧模型输出残留。"""

        await session.execute(delete(WikiPageAlias).where(WikiPageAlias.page_id == page_id))
        title_key = normalize_wiki_alias(title)
        normalized: set[str] = set()
        for alias in aliases:
            key = normalize_wiki_alias(alias)
            if not key or key == title_key or key in normalized:
                continue
            normalized.add(key)
            session.add(
                WikiPageAlias(page_id=page_id, alias=alias.strip(), normalized_alias=key)
            )

    @staticmethod
    async def _rebuild_index(session: AsyncSession, knowledge_base_id: UUID) -> None:
        pages = list(
            await session.scalars(
                select(WikiPage)
                .where(
                    WikiPage.knowledge_base_id == knowledge_base_id,
                    WikiPage.page_type.in_(["summary", "topic"]),
                    WikiPage.current_version_id.is_not(None),
                    WikiPage.deleted_at.is_(None),
                )
                .order_by(WikiPage.page_type, WikiPage.title)
            )
        )
        lines = ["# Wiki 目录", "", "## 主题"]
        lines.extend(
            f"- [[{page.slug}|{page.title}]]" for page in pages if page.page_type == "topic"
        )
        lines.append("\n## 文档摘要")
        lines.extend(
            f"- [[{page.slug}|{page.title}]]" for page in pages if page.page_type == "summary"
        )
        await WikiProcessor._upsert_page(
            session,
            knowledge_base_id,
            slug="index/home",
            page_type="index",
            title="Wiki 目录",
            content="\n".join(lines),
            summary="知识库 Wiki 目录",
            metadata={},
        )
