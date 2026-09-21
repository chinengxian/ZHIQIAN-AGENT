from agent_api.knowledge.application.ingestion import (
    ChunkDraft,
    ParentChildChunker,
    ParsedSection,
)
from agent_api.knowledge.domain.statuses import ChunkKind


def test_parent_child_chunker_preserves_heading_and_parent_relationship() -> None:
    sections = [
        ParsedSection(
            text="第一段内容。第二段内容。第三段内容。",
            heading_path=("产品手册", "安装"),
            page_start=2,
            page_end=2,
        )
    ]

    chunks = ParentChildChunker(max_child_characters=8).chunk(sections)

    assert chunks[0] == ChunkDraft(
        ordinal=0,
        kind=ChunkKind.PARENT,
        content="第一段内容。第二段内容。第三段内容。",
        embedding_text=None,
        heading_path=("产品手册", "安装"),
        page_start=2,
        page_end=2,
        char_start=0,
        char_end=18,
        parent_ordinal=None,
    )
    children = chunks[1:]
    assert len(children) >= 2
    assert all(chunk.kind is ChunkKind.CHILD for chunk in children)
    assert all(chunk.parent_ordinal == 0 for chunk in children)
    assert all(chunk.heading_path == ("产品手册", "安装") for chunk in children)
    assert "".join(chunk.content for chunk in children) == chunks[0].content
    assert all(chunk.embedding_text.startswith("产品手册 > 安装\n") for chunk in children)


def test_parent_child_chunker_skips_blank_sections_and_keeps_stable_ordinals() -> None:
    chunks = ParentChildChunker(max_child_characters=100).chunk(
        [
            ParsedSection(text="   ", heading_path=()),
            ParsedSection(text="有效内容", heading_path=("标题",)),
        ]
    )

    assert [(chunk.ordinal, chunk.kind, chunk.parent_ordinal) for chunk in chunks] == [
        (0, ChunkKind.PARENT, None),
        (1, ChunkKind.CHILD, 0),
    ]
