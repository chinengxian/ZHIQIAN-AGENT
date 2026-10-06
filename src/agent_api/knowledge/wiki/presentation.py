from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from agent_api.knowledge.infrastructure.database.models import Chunk

_METADATA_LABELS = {
    "document_id": "文档编号",
    "version": "版本",
    "effective_date": "生效日期",
    "status": "状态",
    "scope": "适用范围",
    "owner": "责任部门",
    "supersedes": "替代版本",
    "tags": "标签",
}
_FRONT_MATTER_KEYS = {*_METADATA_LABELS, "title"}
_FRONT_MATTER_LINE = re.compile(r"^(?P<key>[a-z_]+):(?P<value>.*)$")
_HEADING = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class WikiMetadataItem:
    """可安全展示在 Wiki 信息卡中的文档元数据。"""

    key: str
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class WikiPresentation:
    """摘要页的标题与白名单信息卡数据。"""

    title: str
    metadata: tuple[WikiMetadataItem, ...]


def extract_wiki_presentation(
    fallback_title: str,
    chunks: Sequence[Chunk],
) -> WikiPresentation:
    """从首个非空分块安全提取标题和有限的 Front Matter。"""

    first_chunk = next((chunk for chunk in chunks if chunk.content.strip()), None)
    if first_chunk is None:
        return WikiPresentation(title=fallback_title.strip(), metadata=())
    front_matter = _parse_front_matter(first_chunk.content)
    title = _normalized_value(front_matter.get("title", ""))
    if not title and first_chunk.heading_path:
        title = _normalized_value(first_chunk.heading_path[0])
    if not title:
        heading = _HEADING.search(first_chunk.content)
        if heading is not None:
            title = _normalized_value(heading.group("title"))
    metadata = tuple(
        WikiMetadataItem(key=key, label=_METADATA_LABELS[key], value=value)
        for key, value in front_matter.items()
        if key in _METADATA_LABELS
    )
    return WikiPresentation(title=title or fallback_title.strip(), metadata=metadata)


def _parse_front_matter(content: str) -> dict[str, str]:
    """只支持首段成对边界内的单行键值，避免执行 YAML 输入。"""

    normalized = content.lstrip()
    if not normalized.startswith("---"):
        return {}
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"
        )
    except StopIteration:
        return {}
    values: dict[str, str] = {}
    for line in lines[1:closing_index]:
        match = _FRONT_MATTER_LINE.fullmatch(line.strip())
        if match is None:
            continue
        key = match.group("key")
        if key not in _FRONT_MATTER_KEYS or key in values:
            continue
        value = _normalized_value(match.group("value"))
        if not value or len(value) > 200:
            continue
        values[key] = _normalize_tags(value) if key == "tags" else value
    return values


def _normalized_value(value: str) -> str:
    """去除展示噪声，保持输入文本为纯字符串。"""

    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1].strip()
    return normalized


def _normalize_tags(value: str) -> str:
    """将简单列表或逗号分隔标签统一为可读文本。"""

    source = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    tags = [_normalized_value(item) for item in source.split(",")]
    return ", ".join(tag for tag in tags if tag)
