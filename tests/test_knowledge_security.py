from pathlib import Path
from uuid import UUID

import pytest

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.worker.cleanup import KnowledgeCleanupService


def test_cleanup_rejects_escape_path_without_touching_file(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    storage.ensure_ready()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    cleanup = KnowledgeCleanupService(None, storage, None)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="stored_file_path_invalid"):
        cleanup._delete_stored_file("../secret.txt")

    assert outside.read_text() == "secret"


def test_public_source_omits_storage_path_and_embedding() -> None:
    source = Source(
        citation_id="[1]",
        chunk_id=UUID(int=1),
        document_id=UUID(int=2),
        document_version_id=UUID(int=3),
        title="指南",
        filename="guide.pdf",
        page_start=2,
        page_end=2,
        heading_path=("安装",),
        excerpt="正文片段",
        score=0.8,
        rank=1,
    )
    payload = source.public_dict()
    assert payload["citation_id"] == "[1]"
    assert not {"file_path", "storage_path", "embedding", "dense_vector"} & payload.keys()
