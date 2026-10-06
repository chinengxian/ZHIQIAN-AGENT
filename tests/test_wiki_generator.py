from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent_api.knowledge.wiki.generator import ChatWikiGenerator


async def test_generator_keeps_global_chunk_sources_across_batches() -> None:
    """第二批输出必须继续引用原文的全局分块编号。"""

    model = FakeListChatModel(
        responses=[
            '{"summary_claims":[{"text":"首批事实","source_indices":[1]}],'
            '"topics":[{"name":"身份认证","aliases":["MFA"],"source_indices":[1]}]}',
            '{"summary_claims":[{"text":"尾部事实","source_indices":[13]}],'
            '"topics":[{"name":"身份认证","aliases":["多因素认证"],'
            '"source_indices":[13]}]}',
        ]
    )

    result = await ChatWikiGenerator(model, "fake").generate(
        "规范", [f"分块 {index}" for index in range(1, 14)]
    )

    assert [claim.source_indices for claim in result.summary_claims] == [(1,), (13,)]
    assert result.topics[0].aliases == ("MFA", "多因素认证")
    assert result.topics[0].source_indices == (1, 13)
    assert result.summary == "首批事实\n尾部事实"


@pytest.mark.parametrize("source_indices", ([], [99], [0]))
async def test_generator_rejects_empty_or_out_of_range_sources(
    source_indices: list[int],
) -> None:
    """无来源或越界来源不能成为可信 Wiki 事实。"""

    payload = (
        '{"summary_claims":[{"text":"无效事实","source_indices":'
        f"{source_indices}".replace("'", '"')
        + '}],"topics":[]}'
    )
    model = FakeListChatModel(responses=[payload])

    with pytest.raises(ValueError, match="wiki_generation_invalid_source"):
        await ChatWikiGenerator(model, "fake").generate("规范", ["唯一分块"])


async def test_generator_rejects_unknown_json_shape() -> None:
    """旧版 summary/topics 字符串协议不能静默进入新发布链路。"""

    model = FakeListChatModel(responses=['{"summary":"旧摘要","topics":[]}'])

    with pytest.raises(ValueError, match="wiki_generation_invalid_shape"):
        await ChatWikiGenerator(model, "fake").generate("规范", ["唯一分块"])
