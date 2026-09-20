from pathlib import Path
from uuid import UUID

_ALLOWED_SUFFIXES = frozenset({".pdf", ".docx", ".md", ".txt"})


class UnsafeStorageRootError(ValueError):
    """存储根目录过宽，可能误写项目或用户目录。"""

    pass


class LocalFileStorage:
    """受控的本地原文件存储。

    调用方只提供系统生成的 UUID 和白名单后缀，原始文件名不会参与路径拼接。
    """

    def __init__(self, root: Path) -> None:
        resolved = root.expanduser().resolve()
        forbidden = {Path.cwd().resolve(), Path.home().resolve()}
        if resolved in forbidden:
            raise UnsafeStorageRootError("storage root must not be the repository or user home")
        self.root = resolved

    def ensure_ready(self) -> Path:
        """创建目录并通过探针验证当前进程具有写权限。"""

        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".write-probe"
        probe.write_bytes(b"")
        probe.unlink()
        return self.root

    def path_for(self, file_id: UUID, *, suffix: str) -> Path:
        """生成并再次校验位于存储根目录下的绝对路径。"""

        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError("unsupported or unsafe file suffix")
        path = (self.root / f"{file_id}{suffix}").resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("file path escapes storage root")
        return path

    def write(self, file_id: UUID, content: bytes, *, suffix: str) -> Path:
        self.ensure_ready()
        destination = self.path_for(file_id, suffix=suffix)
        # 先写临时文件再原子替换，避免读取方看到半写入内容。
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        temporary.write_bytes(content)
        temporary.replace(destination)
        return destination

    def delete(self, file_id: UUID, *, suffix: str) -> None:
        """幂等删除；重复清理不会把“文件不存在”当作任务失败。"""

        self.path_for(file_id, suffix=suffix).unlink(missing_ok=True)
