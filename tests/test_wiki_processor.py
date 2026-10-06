from __future__ import annotations

from uuid import uuid4

import pytest

from agent_api.knowledge.infrastructure.database.models import Chunk
from agent_api.knowledge.infrastructure.database.models import WikiPage
from agent_api.knowledge.wiki.processor import (
    normalize_wiki_alias,
    parse_generation_checkpoint,
    retire_stale_topic_pages,
    render_summary_content,
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
    claims, topics = parse_generation_checkpoint(
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
    )

    assert claims[0].chunks == (chunks[2],)
    assert topics[0].chunks == (chunks[1], chunks[2])
    assert topics[0].aliases == ("ＭＦＡ", "双因素校验")


def test_generation_checkpoint_rejects_stale_source_index() -> None:
    """检查点若引用已不存在的分块，任务不能继续发布。"""

    with pytest.raises(ValueError, match="wiki_generation_invalid_source"):
        parse_generation_checkpoint(
            {
                "summary_claims": [{"text": "越界事实", "source_indices": [2]}],
                "topics": [],
            },
            [_chunk(0, "唯一分块")],
        )


def test_summary_content_contains_claims_and_locators_not_chunk_prefixes() -> None:
    """正文由事实和引用定位组成，不再复制每个分块的前 350 字。"""

    chunks = [_chunk(0, "只存在于未被引用分块的前缀"), _chunk(1, "被引用的长原文")]
    claims, _ = parse_generation_checkpoint(
        {
            "summary_claims": [{"text": "可验证事实", "source_indices": [2]}],
            "topics": [],
        },
        chunks,
    )

    content = render_summary_content("安全规范", claims)

    assert "可验证事实 [^claim-1-source-1]" in content
    assert "原文分块 2" in content
    assert "只存在于未被引用分块的前缀" not in content
    assert "被引用的长原文" not in content


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
