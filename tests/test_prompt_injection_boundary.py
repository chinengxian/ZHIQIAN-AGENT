from agent_api.knowledge.application.citations import CitationStreamFilter
from agent_api.llm.agent import KNOWLEDGE_SYSTEM_PROMPT


def test_cross_chunk_citations_only_use_registered_sources() -> None:
    citations = CitationStreamFilter()
    assert citations.push("依据[", 1) == "依据"
    assert citations.push("1]，另见[", 1) == "[1]，另见"
    assert citations.push("99]。", 1) == "。"
    assert citations.finish() == ""


def test_no_evidence_cannot_emit_citation_marker() -> None:
    citations = CitationStreamFilter()
    assert citations.push("未找到证据[1]，但保留[说明]。", 0) == "未找到证据，但保留[说明]。"
    assert "不可信数据" in KNOWLEDGE_SYSTEM_PROMPT
    assert "不得编造来源" in KNOWLEDGE_SYSTEM_PROMPT
