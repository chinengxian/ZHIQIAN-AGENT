from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Protocol
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from agent_api.knowledge.domain.statuses import ChunkKind
from agent_api.knowledge.infrastructure.milvus.index import IndexedChild

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


class UploadValidationError(ValueError):
    """可安全返回给 API 调用方的稳定上传错误。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    suffix: str
    mime_type: str
    sha256: str
    title: str
    size: int


def validate_upload(
    filename: str,
    declared_mime_type: str,
    content: bytes,
    *,
    max_bytes: int,
) -> ValidatedUpload:
    """同时校验扩展名、声明 MIME、文件签名、编码和大小。"""

    suffix = Path(filename).suffix.lower()
    expected_mime = _MIME_BY_SUFFIX.get(suffix)
    if expected_mime is None:
        raise UploadValidationError("unsupported_file_type")
    if declared_mime_type.split(";", 1)[0].strip().lower() != expected_mime:
        raise UploadValidationError("unsupported_media_type")
    if not content:
        raise UploadValidationError("empty_file")
    if len(content) > max_bytes:
        raise UploadValidationError("file_too_large")

    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise UploadValidationError("invalid_file_signature")
    if suffix == ".docx":
        try:
            with ZipFile(BytesIO(content)) as archive:
                names = frozenset(archive.namelist())
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    raise UploadValidationError("invalid_file_signature")
        except BadZipFile:
            raise UploadValidationError("invalid_file_signature") from None
    if suffix in {".md", ".txt"}:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise UploadValidationError("invalid_text_encoding") from None

    title = Path(filename).stem.strip() or "Untitled"
    return ValidatedUpload(
        suffix=suffix,
        mime_type=expected_mime,
        sha256=sha256(content).hexdigest(),
        title=title,
        size=len(content),
    )


@dataclass(frozen=True, slots=True)
class ParsedSection:
    text: str
    heading_path: tuple[str, ...]
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    kind: ChunkKind
    content: str
    embedding_text: str | None
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int
    parent_ordinal: int | None


class ParentChildChunker:
    """从结构化段落生成 PostgreSQL 父块与仅供索引的子块草稿。"""

    def __init__(self, *, max_child_characters: int = 1200) -> None:
        if max_child_characters <= 0:
            raise ValueError("max_child_characters must be positive")
        self.max_child_characters = max_child_characters

    def chunk(self, sections: list[ParsedSection]) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for section in sections:
            content = section.text.strip()
            if not content:
                continue
            parent_ordinal = len(drafts)
            drafts.append(
                ChunkDraft(
                    ordinal=parent_ordinal,
                    kind=ChunkKind.PARENT,
                    content=content,
                    embedding_text=None,
                    heading_path=section.heading_path,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    char_start=0,
                    char_end=len(content),
                    parent_ordinal=None,
                )
            )
            heading = " > ".join(section.heading_path)
            for start in range(0, len(content), self.max_child_characters):
                child_text = content[start : start + self.max_child_characters]
                embedding_text = f"{heading}\n{child_text}" if heading else child_text
                drafts.append(
                    ChunkDraft(
                        ordinal=len(drafts),
                        kind=ChunkKind.CHILD,
                        content=child_text,
                        embedding_text=embedding_text,
                        heading_path=section.heading_path,
                        page_start=section.page_start,
                        page_end=section.page_end,
                        char_start=start,
                        char_end=start + len(child_text),
                        parent_ordinal=parent_ordinal,
                    )
                )
        return drafts


class VersionActivationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class VersionRepository(Protocol):
    async def activate(
        self,
        document_id: UUID,
        version_id: UUID,
        previous_version_id: UUID | None,
    ) -> None: ...

    async def mark_failed(self, version_id: UUID, code: str) -> None: ...


class VersionIndex(Protocol):
    async def count_version(self, version_id: UUID) -> int: ...


class VersionActivator:
    """Milvus 完整性验证通过后才切换 PostgreSQL 活动版本。"""

    def __init__(self, repository: VersionRepository, index: VersionIndex) -> None:
        self._repository = repository
        self._index = index

    async def activate(
        self,
        document_id: UUID,
        version_id: UUID,
        *,
        previous_version_id: UUID | None,
        expected_child_count: int,
    ) -> None:
        indexed_count = await self._index.count_version(version_id)
        if indexed_count != expected_child_count:
            code = "index_verification_failed"
            await self._repository.mark_failed(version_id, code)
            raise VersionActivationError(code)
        await self._repository.activate(document_id, version_id, previous_version_id)


@dataclass(frozen=True, slots=True)
class IngestionContext:
    job_id: UUID
    document_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    file_path: Path
    previous_active_version_id: UUID | None


@dataclass(frozen=True, slots=True)
class PersistedChunk:
    id: UUID
    draft: ChunkDraft
    parent_chunk_id: UUID | None = None


class IngestionRepository(VersionRepository, Protocol):
    async def load_context(self, job_id: UUID) -> IngestionContext: ...

    async def heartbeat(self, job_id: UUID) -> None: ...

    async def update_stage(self, job_id: UUID, stage: str, progress: int) -> None: ...

    async def replace_chunks(
        self,
        version_id: UUID,
        drafts: list[ChunkDraft],
    ) -> list[PersistedChunk]: ...

    async def complete_job(self, job_id: UUID) -> None: ...

    async def fail_job(self, job_id: UUID, code: str) -> None: ...

    async def schedule_failed_version_cleanup(self, version_id: UUID) -> None: ...


class DocumentParser(Protocol):
    def parse(self, path: Path) -> list[ParsedSection]: ...


class DocumentEmbedder(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class IngestionIndex(VersionIndex, Protocol):
    async def replace_version(
        self,
        version_id: UUID,
        children: list[IndexedChild],
    ) -> None: ...

    async def delete_version(self, version_id: UUID) -> None: ...


class IngestionPipeline:
    """执行解析、父子分块、Embedding、索引校验和原子版本切换。"""

    def __init__(
        self,
        *,
        repository: IngestionRepository,
        parser: DocumentParser,
        chunker: ParentChildChunker,
        embedder: DocumentEmbedder,
        index: IngestionIndex,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds_must_be_positive")
        self._repository = repository
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._index = index
        self._activator = VersionActivator(repository, index)
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    async def _renew_heartbeat(self, job_id: UUID) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            try:
                await self._repository.heartbeat(job_id)
            except Exception:
                # 心跳故障不打断正在进行的摄取；阶段更新和持久化重试仍负责恢复。
                continue

    async def run(self, job_id: UUID) -> None:
        try:
            context = await self._repository.load_context(job_id)
        except Exception:
            # 此处尚无版本上下文；交由队列重试，避免泄漏连接串或文件路径。
            raise IngestionPipelineError("ingestion_unavailable") from None
        heartbeat_task = asyncio.create_task(self._renew_heartbeat(job_id))
        index_started = False
        activated = False
        try:
            await self._repository.update_stage(job_id, "parsing", 10)
            sections = await asyncio.to_thread(self._parser.parse, context.file_path)

            await self._repository.update_stage(job_id, "chunking", 30)
            drafts = self._chunker.chunk(sections)
            persisted = await self._repository.replace_chunks(context.document_version_id, drafts)
            children = [chunk for chunk in persisted if chunk.draft.kind is ChunkKind.CHILD]

            await self._repository.update_stage(job_id, "embedding", 50)
            texts = [chunk.draft.embedding_text or chunk.draft.content for chunk in children]
            vectors = await self._embedder.embed_documents(texts)
            if len(vectors) != len(children):
                raise RuntimeError("embedding_count_mismatch")

            indexed_children = [
                IndexedChild(
                    chunk_id=chunk.id,
                    document_version_id=context.document_version_id,
                    knowledge_base_id=context.knowledge_base_id,
                    content=chunk.draft.content,
                    dense_vector=tuple(vector),
                )
                for chunk, vector in zip(children, vectors, strict=True)
            ]
            await self._repository.update_stage(job_id, "indexing", 75)
            index_started = True
            await self._index.replace_version(context.document_version_id, indexed_children)
            await self._activator.activate(
                context.document_id,
                context.document_version_id,
                previous_version_id=context.previous_active_version_id,
                expected_child_count=len(indexed_children),
            )
            activated = True
            await self._repository.update_stage(job_id, "ready", 100)
            await self._repository.complete_job(job_id)
        except Exception as error:
            raw_code = getattr(error, "code", None)
            code = raw_code if isinstance(raw_code, str) else "ingestion_failed"
            if not activated:
                if index_started:
                    try:
                        await self._index.delete_version(context.document_version_id)
                    except Exception:
                        # 删除索引失败时交给持久化 outbox 延迟补偿。
                        await self._repository.schedule_failed_version_cleanup(
                            context.document_version_id
                        )
                await self._repository.mark_failed(context.document_version_id, code)
            await self._repository.fail_job(job_id, code)
            raise IngestionPipelineError(code) from None
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


class IngestionPipelineError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
