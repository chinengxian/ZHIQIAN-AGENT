from pathlib import Path
from uuid import UUID

_ALLOWED_SUFFIXES = frozenset({".pdf", ".docx", ".md", ".txt"})


class UnsafeStorageRootError(ValueError):
    pass


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        resolved = root.expanduser().resolve()
        forbidden = {Path.cwd().resolve(), Path.home().resolve()}
        if resolved in forbidden:
            raise UnsafeStorageRootError("storage root must not be the repository or user home")
        self.root = resolved

    def ensure_ready(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".write-probe"
        probe.write_bytes(b"")
        probe.unlink()
        return self.root

    def path_for(self, file_id: UUID, *, suffix: str) -> Path:
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError("unsupported or unsafe file suffix")
        path = (self.root / f"{file_id}{suffix}").resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("file path escapes storage root")
        return path

    def write(self, file_id: UUID, content: bytes, *, suffix: str) -> Path:
        self.ensure_ready()
        destination = self.path_for(file_id, suffix=suffix)
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        temporary.write_bytes(content)
        temporary.replace(destination)
        return destination

    def delete(self, file_id: UUID, *, suffix: str) -> None:
        self.path_for(file_id, suffix=suffix).unlink(missing_ok=True)
