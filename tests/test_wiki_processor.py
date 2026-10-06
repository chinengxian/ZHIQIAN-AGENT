from __future__ import annotations

from uuid import uuid4

import pytest

from agent_api.knowledge.infrastructure.database.models import Chunk, WikiPage
from agent_api.knowledge.wiki.processor import (
    GroundedSection,
    normalize_wiki_alias,
    parse_generation_checkpoint,
    render_summary_content,
    retire_stale_topic_pages,
)


def _chunk(ordinal: int, content: str) -> Chunk:
    return Chunk(
        id=uuid4(),
        document_version_id=uuid4(),
        ordinal=ordinal,
        kind="child",
        content=content,
        token_count=10,
    )


def test_generation_checkpoint_maps_claims_and_topics_to_exact_chunks() -> None:
    """发布前必须把模型编号重新映射到当前加载的真实分块。"""

    chunks = [_chunk(0, "首段"), _chunk(1, "中段"), _chunk(2, "尾部证据")]
    _, claims, topics = parse_generation_checkpoint(
        {
            "summary_claims": [{"text": "尾部事实", "source_indices": [3]}],
            "topics": [
                {
                    "name": "身份认证",
                    "aliases": ["ＭＦＡ", "双因素校验"],
                    "source_indices": [2, 3],
                }
            ],
        },
        chunks,
        allow_legacy_sections=True,
    )

    assert claims[0].chunks == (chunks[2],)
    assert topics[0].chunks == (chunks[1], chunks[2])
    assert topics[0].aliases == ("ＭＦＡ", "双因素校验")


def test_generation_checkpoint_maps_summary_sections_to_exact_chunks() -> None:
    """摘要段落与原子事实必须各自绑定精确来源。"""

    chunks = [_chunk(0, "开头"), _chunk(1, "中段"), _chunk(2, "尾部")]
    sections, claims, _ = parse_generation_checkpoint(
        {
            "summary_sections": [
                {
                    "title": "概述",
                    "text": "制度覆盖身份与访问控制。",
                    "source_indices": [1, 3],
                }
            ],
            "summary_claims": [
                {"text": "必须使用唯一身份。", "source_indices": [2]}
            ],
            "topics": [],
        },
        chunks,
    )

    assert sections[0].chunks == (chunks[0], chunks[2])
    assert claims[0].chunks == (chunks[1],)


def test_generation_checkpoint_only_allows_missing_sections_for_v1() -> None:
    """新版任务不能把缺少分段的检查点静默发布为旧格式。"""

    data = {
        "summary_claims": [{"text": "唯一身份。", "source_indices": [1]}],
        "topics": [],
    }
    chunks = [_chunk(0, "证据")]

    with pytest.raises(ValueError, match="wiki_generation_invalid_shape"):
        parse_generation_checkpoint(data, chunks)

    sections, claims, topics = parse_generation_checkpoint(
        data,
        chunks,
        allow_legacy_sections=True,
    )

    assert sections == ()
    assert claims[0].text == "唯一身份。"
    assert topics == ()


def test_generation_checkpoint_rejects_stale_source_index() -> None:
    """检查点若引用已不存在的分块，任务不能继续发布。"""

    with pytest.raises(ValueError, match="wiki_generation_invalid_source"):
        parse_generation_checkpoint(
            {
                "summary_claims": [{"text": "越界事实", "source_indices": [2]}],
                "topics": [],
            },
            [_chunk(0, "唯一分块")],
            allow_legacy_sections=True,
        )


def test_summary_content_contains_only_readable_sections() -> None:
    """新版摘要正文只展示连贯段落，不暴露 Markdown 引用标记。"""

    sections = (
        GroundedSection("概述", "本规范覆盖身份与访问控制。", (_chunk(0, "来源"),)),
        GroundedSection("核心要求", "特权操作必须启用多因素认证。", (_chunk(1, "来源"),)),
    )

    content = render_summary_content("安全规范", sections)

    assert content == "## 概述\n\n本规范覆盖身份与访问控制。\n\n## 核心要求\n\n特权操作必须启用多因素认证。"
    assert "[^" not in content
    assert "# 安全规范" not in content


def test_alias_normalization_handles_width_case_punctuation_and_spaces() -> None:
    assert normalize_wiki_alias("  ＭＦＡ---Control　策略 ") == "mfa control 策略"


async def test_rebuild_retires_topics_that_no_longer_have_enough_documents() -> None:
    knowledge_base_id = uuid4()
    current = WikiPage(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        slug="topic/current",
        page_type="topic",
        title="当前主题",
    )
    stale = WikiPage(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        slug="topic/stale",
        page_type="topic",
        title="过期主题",
    )

    class FakeSession:
        def __init__(self) -> None:
            self.alias_delete_calls = 0

        async def scalars(self, statement: object) -> list[WikiPage]:
            del statement
            return [current, stale]

        async def execute(self, statement: object) -> None:
            del statement
            self.alias_delete_calls += 1

    session = FakeSession()
    await retire_stale_topic_pages(
        session,  # type: ignore[arg-type]
        knowledge_base_id,
        {"topic/current"},
    )

    assert current.deleted_at is None
    assert stale.deleted_at is not None
    assert session.alias_delete_calls == 1
