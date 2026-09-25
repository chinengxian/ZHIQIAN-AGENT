import asyncio
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from agent_api.knowledge.application.ingestion import (
    ChunkDraft,
    IngestionContext,
    IngestionPipeline,
    IngestionPipelineError,
    ParentChildChunker,
    ParsedSection,
    PersistedChunk,
    UploadValidationError,
    validate_upload,
)
from agent_api.knowledge.infrastructure.milvus.index import IndexedChild

JOB_ID = UUID("80000000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("80000000-0000-0000-0000-000000000002")
VERSION_ID = UUID("80000000-0000-0000-0000-000000000003")
OLD_VERSION_ID = UUID("80000000-0000-0000-0000-000000000004")
KB_ID = UUID("80000000-0000-0000-0000-000000000005")
PARENT_ID = UUID("80000000-0000-0000-0000-000000000006")
CHILD_ID = UUID("80000000-0000-0000-0000-000000000007")


def minimal_docx() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "mime_type", "content", "expected_mime"),
    [
        ("guide.pdf", "application/pdf", b"%PDF-1.7\nfixture", "application/pdf"),
        (
            "guide.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            minimal_docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("guide.md", "text/markdown", "# 标题\n正文".encode(), "text/markdown"),
        ("guide.txt", "text/plain", "纯文本".encode(), "text/plain"),
    ],
)
def test_validate_upload_accepts_supported_signatures(
    filename: str,
    mime_type: str,
    content: bytes,
    expected_mime: str,
) -> None:
    result = validate_upload(filename, mime_type, content, max_bytes=1024 * 1024)

    assert result.mime_type == expected_mime
    assert result.suffix == f".{filename.rsplit('.', 1)[1]}"
    assert len(result.sha256) == 64
    assert result.title == "guide"


@pytest.mark.parametrize(
    ("filename", "mime_type", "content", "code"),
    [
        ("payload.exe", "application/octet-stream", b"MZ", "unsupported_file_type"),
        ("fake.pdf", "application/pdf", b"not a pdf", "invalid_file_signature"),
        (
            "fake.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PKbroken",
            "invalid_file_signature",
        ),
        ("binary.txt", "text/plain", b"\xff\xfe", "invalid_text_encoding"),
        ("empty.md", "text/markdown", b"", "empty_file"),
    ],
)
def test_validate_upload_rejects_unsafe_or_corrupt_files(
    filename: str,
    mime_type: str,
    content: bytes,
    code: str,
) -> None:
    with pytest.raises(UploadValidationError) as caught:
        validate_upload(filename, mime_type, content, max_bytes=1024)

    assert caught.value.code == code
    assert filename not in str(caught.value)


def test_validate_upload_rejects_declared_mime_mismatch_and_oversize() -> None:
    with pytest.raises(UploadValidationError, match="unsupported_media_type"):
        validate_upload("guide.pdf", "text/plain", b"%PDF-1.7", max_bytes=1024)

    with pytest.raises(UploadValidationError, match="file_too_large"):
        validate_upload("guide.txt", "text/plain", b"large", max_bytes=4)


class FakeParser:
    def parse(self, path: Path) -> list[ParsedSection]:
        assert path.name == "version.md"
        return [ParsedSection("需要索引的正文", ("指南",), page_start=1, page_end=1)]


class FakeEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.texts = texts
        return [[0.1, 0.2] for _ in texts]


class FakePipelineRepository:
    def __init__(self, file_path: Path) -> None:
        self.context = IngestionContext(
            job_id=JOB_ID,
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_ID,
            knowledge_base_id=KB_ID,
            file_path=file_path,
            previous_active_version_id=OLD_VERSION_ID,
        )
        self.stages: list[tuple[str, int]] = []
        self.activated: list[UUID] = []
        self.completed = False
        self.failures: list[tuple[UUID, str]] = []
        self.scheduled_cleanup: list[UUID] = []
        self.heartbeats = 0

    async def load_context(self, job_id: UUID) -> IngestionContext:
        assert job_id == JOB_ID
        return self.context

    async def update_stage(self, job_id: UUID, stage: str, progress: int) -> None:
        self.stages.append((stage, progress))

    async def replace_chunks(
        self, version_id: UUID, drafts: list[ChunkDraft]
    ) -> list[PersistedChunk]:
        assert version_id == VERSION_ID
        return [
            PersistedChunk(PARENT_ID, drafts[0]),
            PersistedChunk(CHILD_ID, drafts[1], parent_chunk_id=PARENT_ID),
        ]

    async def activate(
        self,
        document_id: UUID,
        version_id: UUID,
        previous_version_id: UUID | None,
    ) -> None:
        assert (document_id, previous_version_id) == (DOCUMENT_ID, OLD_VERSION_ID)
        self.activated.append(version_id)

    async def mark_failed(self, version_id: UUID, code: str) -> None:
        self.failures.append((version_id, code))

    async def fail_job(self, job_id: UUID, code: str) -> None:
        self.failures.append((job_id, code))

    async def complete_job(self, job_id: UUID) -> None:
        self.completed = True

    async def heartbeat(self, job_id: UUID) -> None:
        self.heartbeats += 1

    async def schedule_failed_version_cleanup(self, version_id: UUID) -> None:
        self.scheduled_cleanup.append(version_id)


class FakePipelineIndex:
    def __init__(self) -> None:
        self.children: list[IndexedChild] = []

    async def replace_version(self, version_id: UUID, children: list[IndexedChild]) -> None:
        assert version_id == VERSION_ID
        self.children = children

    async def count_version(self, version_id: UUID) -> int:
        return len(self.children)

    async def delete_version(self, version_id: UUID) -> None:
        self.children = []


async def test_ingestion_pipeline_orders_stages_and_indexes_only_children(tmp_path: Path) -> None:
    repository = FakePipelineRepository(tmp_path / "version.md")
    embedder = FakeEmbedder()
    index = FakePipelineIndex()
    pipeline = IngestionPipeline(
        repository=repository,
        parser=FakeParser(),
        chunker=ParentChildChunker(max_child_characters=100),
        embedder=embedder,
        index=index,
    )

    await pipeline.run(JOB_ID)

    assert repository.stages == [
        ("parsing", 10),
        ("chunking", 30),
        ("embedding", 50),
        ("indexing", 75),
        ("ready", 100),
    ]
    assert embedder.texts == ["指南\n需要索引的正文"]
    assert len(index.children) == 1
    assert index.children[0].chunk_id == CHILD_ID
    assert repository.activated == [VERSION_ID]
    assert repository.completed is True
    assert all(child.content != "" for child in index.children)


class FailingParser:
    def parse(self, path: Path) -> list[ParsedSection]:
        error = RuntimeError("internal parser path and details")
        error.code = "document_parse_failed"  # type: ignore[attr-defined]
        raise error


async def test_ingestion_pipeline_records_stable_failure_without_activating(tmp_path: Path) -> None:
    repository = FakePipelineRepository(tmp_path / "version.md")
    index = FakePipelineIndex()
    pipeline = IngestionPipeline(
        repository=repository,
        parser=FailingParser(),
        chunker=ParentChildChunker(),
        embedder=FakeEmbedder(),
        index=index,
    )

    with pytest.raises(IngestionPipelineError, match="document_parse_failed"):
        await pipeline.run(JOB_ID)

    assert repository.activated == []
    assert repository.failures == [
        (VERSION_ID, "document_parse_failed"),
        (JOB_ID, "document_parse_failed"),
    ]
    assert index.children == []


class FailingVerificationAndCleanupIndex(FakePipelineIndex):
    async def count_version(self, version_id: UUID) -> int:
        return 0

    async def delete_version(self, version_id: UUID) -> None:
        raise RuntimeError("milvus unavailable")


async def test_failed_index_cleanup_is_scheduled_without_exposing_internal_error(
    tmp_path: Path,
) -> None:
    repository = FakePipelineRepository(tmp_path / "version.md")
    index = FailingVerificationAndCleanupIndex()
    pipeline = IngestionPipeline(
        repository=repository,
        parser=FakeParser(),
        chunker=ParentChildChunker(),
        embedder=FakeEmbedder(),
        index=index,
    )

    with pytest.raises(IngestionPipelineError, match="index_verification_failed"):
        await pipeline.run(JOB_ID)

    assert repository.scheduled_cleanup == [VERSION_ID]
    assert repository.activated == []


class UnavailableRepository(FakePipelineRepository):
    async def load_context(self, job_id: UUID) -> IngestionContext:
        raise OSError("postgresql://secret@127.0.0.1")


class AlreadyClaimedRepository(FakePipelineRepository):
    async def load_context(self, job_id: UUID) -> IngestionContext:
        from agent_api.knowledge.application.ingestion import IngestionAlreadyClaimed

        raise IngestionAlreadyClaimed()


async def test_duplicate_delivery_is_acknowledged_without_reprocessing(tmp_path: Path) -> None:
    repository = AlreadyClaimedRepository(tmp_path / "version.md")
    parser = FakeParser()
    pipeline = IngestionPipeline(
        repository=repository,
        parser=parser,
        chunker=ParentChildChunker(),
        embedder=FakeEmbedder(),
        index=FakePipelineIndex(),
    )

    await pipeline.run(JOB_ID)

    assert repository.stages == []
    assert repository.failures == []


async def test_database_connection_failure_is_retryable_and_redacted(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(
        repository=UnavailableRepository(tmp_path / "version.md"),
        parser=FakeParser(),
        chunker=ParentChildChunker(),
        embedder=FakeEmbedder(),
        index=FakePipelineIndex(),
    )

    with pytest.raises(IngestionPipelineError, match="ingestion_unavailable") as caught:
        await pipeline.run(JOB_ID)

    assert "secret" not in str(caught.value)


class WaitingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.entered.set()
        await self.release.wait()
        return await super().embed_documents(texts)


async def test_long_embedding_stage_renews_job_heartbeat(tmp_path: Path) -> None:
    repository = FakePipelineRepository(tmp_path / "version.md")
    embedder = WaitingEmbedder()
    pipeline = IngestionPipeline(
        repository=repository,
        parser=FakeParser(),
        chunker=ParentChildChunker(),
        embedder=embedder,
        index=FakePipelineIndex(),
        heartbeat_interval_seconds=0.01,
    )
    task = asyncio.create_task(pipeline.run(JOB_ID))
    try:
        await asyncio.wait_for(embedder.entered.wait(), timeout=1)
        await asyncio.sleep(0.05)
        assert repository.heartbeats >= 1
    finally:
        embedder.release.set()
        await task
