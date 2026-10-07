from pathlib import Path

import pytest

from agent_api.knowledge.wiki.indexing import (
    embedding_fingerprint,
    relevance_fingerprint,
)
from tests.test_knowledge_config import knowledge_settings


def test_embedding_fingerprint_is_stable_for_same_configuration(tmp_path: Path) -> None:
    first = knowledge_settings(tmp_path)
    second = knowledge_settings(tmp_path)

    assert embedding_fingerprint(first) == embedding_fingerprint(second)
    assert len(embedding_fingerprint(first)) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("embedding_provider", "openai"),
        ("embedding_model", "text-embedding-other"),
        ("embedding_dimension", 2048),
        ("wiki_index_protocol_version", "wiki-index-v2"),
    ],
)
def test_embedding_fingerprint_changes_with_index_compatibility_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    baseline = knowledge_settings(tmp_path)
    overrides: dict[str, object] = {field: value}
    if field == "embedding_provider":
        overrides["embedding_base_url"] = "https://embedding.example.test/v1"
    changed = knowledge_settings(tmp_path, **overrides)

    assert embedding_fingerprint(baseline) != embedding_fingerprint(changed)


def test_relevance_fingerprint_is_stable_for_same_configuration(tmp_path: Path) -> None:
    first = knowledge_settings(tmp_path, rerank_provider="none", rerank_model=None)
    second = knowledge_settings(tmp_path, rerank_provider="none", rerank_model=None)

    assert relevance_fingerprint(first) == relevance_fingerprint(second)
    assert len(relevance_fingerprint(first)) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("embedding_model", "text-embedding-other"),
        ("rerank_provider", "none"),
        ("rerank_model", "BAAI/bge-reranker-v2.5"),
        ("wiki_dense_threshold", 0.60),
        ("wiki_rerank_threshold", 0.60),
        ("wiki_keyword_coverage_threshold", 0.60),
        ("wiki_relevance_profile", "wiki-relevance-v2"),
    ],
)
def test_relevance_fingerprint_changes_with_relevance_policy_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    baseline = knowledge_settings(
        tmp_path,
        rerank_provider="local_bge",
        rerank_model="BAAI/bge-reranker-v2-m3",
    )
    changed_values: dict[str, object] = {
        "rerank_provider": "local_bge",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
    }
    changed_values[field] = value
    changed = knowledge_settings(tmp_path, **changed_values)

    assert relevance_fingerprint(baseline) != relevance_fingerprint(changed)
