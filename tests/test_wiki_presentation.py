from __future__ import annotations

from uuid import uuid4

from agent_api.knowledge.infrastructure.database.models import Chunk
from agent_api.knowledge.wiki.presentation import extract_wiki_presentation


def _chunk(ordinal: int, content: str, *, heading_path: list[str] | None = None) -> Chunk:
    return Chunk(
        id=uuid4(),
        document_version_id=uuid4(),
        ordinal=ordinal,
        kind="child",
        content=content,
        token_count=10,
        heading_path=heading_path or [],
    )


def test_extract_presentation_uses_front_matter_title_and_whitelist() -> None:
    """标题和信息卡只来自受限的 Front Matter 字段。"""

    presentation = extract_wiki_presentation(
        fallback_title="02-identity-access-control",
        chunks=[
            _chunk(
                0,
                "---\ntitle: 身份与访问控制规范\nversion: '3.0'\n"
                "tags: [身份管理, MFA]\nsecret: 不展示\n---\n# 身份与访问控制规范",
            )
        ],
    )

    assert presentation.title == "身份与访问控制规范"
    assert [(item.key, item.value) for item in presentation.metadata] == [
        ("version", "3.0"),
        ("tags", "身份管理, MFA"),
    ]


def test_extract_presentation_uses_heading_then_fallback_title() -> None:
    """没有 Front Matter 标题时按标题路径、正文标题、文件名依次回退。"""

    assert (
        extract_wiki_presentation(
            "file-name", [_chunk(0, "正文", heading_path=["路径标题"])]
        ).title
        == "路径标题"
    )
    assert extract_wiki_presentation("file-name", [_chunk(0, "# 可读标题")]).title == "可读标题"
    assert extract_wiki_presentation("file-name", [_chunk(0, "没有标题")]).title == "file-name"


def test_extract_presentation_rejects_unknown_incomplete_and_oversized_metadata() -> None:
    """不完整 Front Matter、未知键和超长值不可进入页面信息卡。"""

    malformed = extract_wiki_presentation(
        "fallback",
        [_chunk(0, "---\nversion: 3.0\n没有结束边界")],
    )
    oversized = extract_wiki_presentation(
        "fallback",
        [_chunk(0, f"---\nowner: {'x' * 201}\nunknown: value\n---\n正文")],
    )

    assert malformed.metadata == ()
    assert oversized.metadata == ()
