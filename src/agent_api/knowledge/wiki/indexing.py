import json
from hashlib import sha256

from agent_api.core.config import Settings


def embedding_fingerprint(settings: Settings) -> str:
    """生成只描述 Embedding 索引兼容性的稳定指纹。"""

    payload = {
        "dimension": settings.embedding_dimension,
        "index_protocol": settings.wiki_index_protocol_version,
        "model": settings.embedding_model or "",
        "provider": settings.embedding_provider.value,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def relevance_fingerprint(settings: Settings) -> str:
    """生成只描述相关性策略且不包含查询或凭据的稳定指纹。"""

    payload = {
        "embedding_fingerprint": embedding_fingerprint(settings),
        "keyword_coverage_threshold": settings.wiki_keyword_coverage_threshold,
        "profile": settings.wiki_relevance_profile,
        "rerank_model": settings.rerank_model or "",
        "rerank_provider": settings.rerank_provider.value,
        "rerank_threshold": settings.wiki_rerank_threshold,
        "dense_threshold": settings.wiki_dense_threshold,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
