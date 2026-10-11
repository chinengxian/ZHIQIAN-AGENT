from __future__ import annotations

import base64
import json
from uuid import UUID, uuid4

import pytest

from agent_api.knowledge.wiki.cursor import (
    PageClaimCursor,
    SearchCursor,
    WikiCursorError,
    decode_cursor,
    decode_page_claim_cursor,
    encode_cursor,
    encode_page_claim_cursor,
)

UNIT_ID = uuid4()
PAGE_ID = uuid4()
PAGE_SORT_KEY = (0, 0, 0, -0.8, str(PAGE_ID))


def cursor_token(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_cursor_rejects_query_or_collection_change() -> None:
    token = encode_cursor(SearchCursor("query-a", "wiki_units_v1", PAGE_SORT_KEY, PAGE_ID))
    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_cursor(token, query_fingerprint="query-b", collection_version="wiki_units_v1")

    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_cursor(token, query_fingerprint="query-a", collection_version="wiki_units_v2")


def test_search_cursor_round_trips_urlsafe_json_payload() -> None:
    page_id = UUID("00000000-0000-0000-0000-000000000001")
    cursor = SearchCursor("query-a", "wiki_units_v1", (0, 0, 0, -0.8, str(page_id)), page_id)

    token = encode_cursor(cursor)

    assert "/" not in token
    assert "+" not in token
    assert (
        decode_cursor(token, query_fingerprint="query-a", collection_version="wiki_units_v1")
        == cursor
    )


@pytest.mark.parametrize(
    "token",
    [
        "not-json",
        encode_cursor(SearchCursor("query-a", "wiki_units_v1", PAGE_SORT_KEY, PAGE_ID))
        + "x" * 2049,
    ],
)
def test_search_cursor_rejects_invalid_or_oversized_payload(token: str) -> None:
    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_cursor(token, query_fingerprint="query-a", collection_version="wiki_units_v1")


@pytest.mark.parametrize("score", [float("nan"), float("inf")])
def test_search_cursor_rejects_non_finite_page_sort_score(score: float) -> None:
    token = cursor_token(
        {
            "query_fingerprint": "query-a",
            "collection_version": "wiki_units_v1",
            "last_page_sort_key": [0, 0, 0, score, str(PAGE_ID)],
            "last_page_id": str(PAGE_ID),
        }
    )

    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_cursor(token, query_fingerprint="query-a", collection_version="wiki_units_v1")


def test_search_cursor_rejects_non_string_token() -> None:
    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_cursor(None, query_fingerprint="query-a", collection_version="wiki_units_v1")


def test_search_cursor_encoder_rejects_non_finite_score() -> None:
    with pytest.raises(ValueError):
        encode_cursor(
            SearchCursor("query-a", "wiki_units_v1", (0, 0, 0, float("nan"), str(PAGE_ID)), PAGE_ID)
        )


def test_page_claim_cursor_round_trips_and_rejects_version_change() -> None:
    page_version_id = uuid4()
    cursor = PageClaimCursor(page_version_id, 42, uuid4())

    token = encode_page_claim_cursor(cursor)

    assert decode_page_claim_cursor(token, page_version_id=page_version_id) == cursor
    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_page_claim_cursor(token, page_version_id=uuid4())


def test_page_claim_cursor_rejects_negative_position() -> None:
    page_version_id = uuid4()
    token = encode_page_claim_cursor(PageClaimCursor(page_version_id, -1, uuid4()))

    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_page_claim_cursor(token, page_version_id=page_version_id)
