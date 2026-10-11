from __future__ import annotations

import base64
import binascii
import json
import math
from dataclasses import dataclass
from typing import Any
from uuid import UUID

MAX_CURSOR_BYTES = 2048
CURSOR_ERROR = "wiki_search_cursor_expired"


@dataclass(frozen=True, slots=True)
class SearchCursor:
    query_fingerprint: str
    collection_version: str
    last_score: float
    last_unit_id: UUID


@dataclass(frozen=True, slots=True)
class PageClaimCursor:
    page_version_id: UUID
    last_position: int
    last_claim_id: UUID


class WikiCursorError(ValueError):
    """Wiki 游标与当前查询或页面版本不兼容。"""


def encode_cursor(cursor: SearchCursor) -> str:
    payload = {
        "query_fingerprint": cursor.query_fingerprint,
        "collection_version": cursor.collection_version,
        "last_score": cursor.last_score,
        "last_unit_id": str(cursor.last_unit_id),
    }
    return _encode_payload(payload)


def decode_cursor(
    token: str,
    *,
    query_fingerprint: str,
    collection_version: str,
) -> SearchCursor:
    payload = _decode_payload(token)
    if set(payload) != {"query_fingerprint", "collection_version", "last_score", "last_unit_id"}:
        raise WikiCursorError(CURSOR_ERROR)
    if payload["query_fingerprint"] != query_fingerprint:
        raise WikiCursorError(CURSOR_ERROR)
    if payload["collection_version"] != collection_version:
        raise WikiCursorError(CURSOR_ERROR)
    if not isinstance(payload["query_fingerprint"], str):
        raise WikiCursorError(CURSOR_ERROR)
    if not isinstance(payload["collection_version"], str):
        raise WikiCursorError(CURSOR_ERROR)
    score = payload["last_score"]
    if not isinstance(score, int | float) or isinstance(score, bool):
        raise WikiCursorError(CURSOR_ERROR)
    normalized_score = float(score)
    if not math.isfinite(normalized_score) or normalized_score < 0:
        raise WikiCursorError(CURSOR_ERROR)
    unit_id = _parse_uuid(payload["last_unit_id"])
    return SearchCursor(
        query_fingerprint=payload["query_fingerprint"],
        collection_version=payload["collection_version"],
        last_score=normalized_score,
        last_unit_id=unit_id,
    )


def encode_page_claim_cursor(cursor: PageClaimCursor) -> str:
    payload = {
        "page_version_id": str(cursor.page_version_id),
        "last_position": cursor.last_position,
        "last_claim_id": str(cursor.last_claim_id),
    }
    return _encode_payload(payload)


def decode_page_claim_cursor(token: str, *, page_version_id: UUID) -> PageClaimCursor:
    payload = _decode_payload(token)
    if set(payload) != {"page_version_id", "last_position", "last_claim_id"}:
        raise WikiCursorError(CURSOR_ERROR)
    decoded_page_version_id = _parse_uuid(payload["page_version_id"])
    if decoded_page_version_id != page_version_id:
        raise WikiCursorError(CURSOR_ERROR)
    position = payload["last_position"]
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise WikiCursorError(CURSOR_ERROR)
    claim_id = _parse_uuid(payload["last_claim_id"])
    return PageClaimCursor(
        page_version_id=decoded_page_version_id,
        last_position=position,
        last_claim_id=claim_id,
    )


def _encode_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_payload(token: str) -> dict[str, Any]:
    if not isinstance(token, str):
        raise WikiCursorError(CURSOR_ERROR)
    if len(token.encode("utf-8")) > MAX_CURSOR_BYTES:
        raise WikiCursorError(CURSOR_ERROR)
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode((token + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (
        binascii.Error,
        UnicodeDecodeError,
        UnicodeEncodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise WikiCursorError(CURSOR_ERROR) from None
    if not isinstance(payload, dict):
        raise WikiCursorError(CURSOR_ERROR)
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid_json_constant:{value}")


def _parse_uuid(value: Any) -> UUID:
    if not isinstance(value, str):
        raise WikiCursorError(CURSOR_ERROR)
    try:
        return UUID(value)
    except ValueError:
        raise WikiCursorError(CURSOR_ERROR) from None
