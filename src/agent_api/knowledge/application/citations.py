import re
from dataclasses import asdict, dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Source:
    citation_id: str
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    filename: str
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]
    excerpt: str
    score: float
    rank: int

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["chunk_id"] = str(self.chunk_id)
        result["document_id"] = str(self.document_id)
        result["document_version_id"] = str(self.document_version_id)
        result["heading_path"] = list(self.heading_path)
        return result


class CitationStreamFilter:
    """仅保留当前轮真实来源的引用标记，并处理跨 chunk 的标记。"""

    def __init__(self) -> None:
        self._pending = ""

    def push(self, content: str, available_count: int) -> str:
        self._pending += content
        output: list[str] = []
        while self._pending:
            start = self._pending.find("[")
            if start < 0:
                output.append(self._pending)
                self._pending = ""
                break
            if start:
                output.append(self._pending[:start])
                self._pending = self._pending[start:]
            end = self._pending.find("]")
            if end < 0:
                if len(self._pending) > 16:
                    output.append(self._pending[0])
                    self._pending = self._pending[1:]
                    continue
                break
            candidate = self._pending[: end + 1]
            if (
                not re.fullmatch(r"\[(\d{1,3})\]", candidate)
                or 1 <= int(candidate[1:-1]) <= available_count
            ):
                output.append(candidate)
            self._pending = self._pending[end + 1 :]
        return "".join(output)

    def finish(self) -> str:
        remaining = self._pending
        self._pending = ""
        return remaining
