from pathlib import Path
from uuid import uuid4

import pytest

from agent_api.knowledge.infrastructure.storage.local import (
    LocalFileStorage,
    UnsafeStorageRootError,
)


def test_storage_writes_uuid_paths_below_controlled_root(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    file_id = uuid4()

    stored = storage.write(file_id, b"document bytes", suffix=".pdf")

    assert stored == storage.root / f"{file_id}.pdf"
    assert stored.read_bytes() == b"document bytes"
    assert stored.is_relative_to(storage.root)


@pytest.mark.parametrize("suffix", ["../secret", "/absolute", ".pdf.exe", "pdf", ".PDF"])
def test_storage_rejects_unsafe_suffixes(tmp_path: Path, suffix: str) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")

    with pytest.raises(ValueError, match="suffix"):
        storage.path_for(uuid4(), suffix=suffix)


def test_storage_rejects_repository_or_home_as_root(tmp_path: Path) -> None:
    repository_root = Path.cwd().resolve()
    home = Path.home().resolve()

    with pytest.raises(UnsafeStorageRootError):
        LocalFileStorage(repository_root)
    with pytest.raises(UnsafeStorageRootError):
        LocalFileStorage(home)

    storage = LocalFileStorage(tmp_path / "uploads")
    assert storage.ensure_ready() == storage.root


def test_storage_delete_is_idempotent(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    file_id = uuid4()
    stored = storage.write(file_id, b"document", suffix=".txt")

    storage.delete(file_id, suffix=".txt")
    storage.delete(file_id, suffix=".txt")

    assert not stored.exists()
