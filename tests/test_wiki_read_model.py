from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

from agent_api.knowledge.infrastructure.database.models import Chunk
from agent_api.knowledge.wiki.read_model import WikiReadModel, build_source_excerpt
from agent_api.knowledge.wiki.service import WikiService


def _chunk(
    *,
    content: str,
    kind: str = "child",
    ordinal: int = 0,
    chunk_id: UUID | None = None,
    version_id: UUID | None = None,
    parent_chunk_id: UUID | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
) -> Chunk:
    return Chunk(
        id=chunk_id or uuid4(),
        document_version_id=version_id or uuid4(),
        parent_chunk_id=parent_chunk_id,
        ordinal=ordinal,
        kind=kind,
        content=content,
        char_start=char_start,
        char_end=char_end,
        token_count=10,
    )


def _row(
    *,
    claim_id: UUID | None = None,
    page_current: bool = True,
    index_state: str = "ready",
    trust_state: str = "verified",
    document_active: bool = True,
    document_ready: bool = True,
    source_kind: str = "child",
    source_ordinal: int = 1,
    source_hash: str | None = None,
    chunk_kind: str = "child",
    chunk_ordinal: int = 1,
    chunk_content: str = "必须启用两个独立认证因素。",
    parent_content: str = "前文。必须启用两个独立认证因素。后文。",
) -> SimpleNamespace:
    page_id = uuid4()
    version_id = uuid4()
    current_version_id = version_id if page_current else uuid4()
    document_id = uuid4()
    document_version_id = uuid4()
    chunk_id = uuid4()
    parent_id = uuid4()
    chunk = _chunk(
        chunk_id=chunk_id,
        version_id=document_version_id,
        parent_chunk_id=parent_id,
        kind=chunk_kind,
        ordinal=chunk_ordinal,
        content=chunk_content,
        char_start=3,
        char_end=3 + len(chunk_content),
    )
    source = SimpleNamespace(
        id=uuid4(),
        claim_id=claim_id or uuid4(),
        document_id=document_id,
        document_version_id=document_version_id,
        chunk_id=chunk_id,
        chunk_kind=source_kind,
        chunk_ordinal=source_ordinal,
        content_sha256=source_hash or sha256(chunk_content.encode("utf-8")).hexdigest(),
        document_title="认证规范",
        heading_path=["认证", "登录"],
        page_start=1,
        page_end=2,
    )
    claim = SimpleNamespace(
        id=source.claim_id,
        page_version_id=version_id,
        claim_key=f"claim-{source.claim_id}",
        text="必须启用 MFA",
        trust_state=trust_state,
        reason=None,
        position=0,
        claim_type="summary_fact",
    )
    document = SimpleNamespace(
        id=document_id,
        title="认证规范",
        filename="auth.md",
        status="ready" if document_ready else "failed",
        deleted_at=None,
        active_version_id=document_version_id if document_active else uuid4(),
    )
    return SimpleNamespace(
        page=SimpleNamespace(
            id=page_id,
            knowledge_base_id=uuid4(),
            slug="topic/auth",
            page_type="topic",
            title="身份认证",
            current_version_id=current_version_id,
        ),
        version=SimpleNamespace(id=version_id, index_state=index_state),
        claim=claim,
        source=source,
        document=document,
        chunk=chunk,
        parent=_chunk(
            chunk_id=parent_id,
            version_id=document_version_id,
            kind="parent",
            ordinal=0,
            content=parent_content,
        ),
    )


class _Rows:
    def __init__(self, rows: Iterable[object]) -> None:
        self._rows = list(rows)

    def all(self) -> list[object]:
        return self._rows


class _FakeSession:
    def __init__(
        self,
        main_rows: list[object],
        parent_rows: list[object] | None = None,
        metadata_rows: list[object] | None = None,
        scalar_rows: list[object] | None = None,
        version: object | None = None,
    ) -> None:
        self._batches = [main_rows, parent_rows or [], metadata_rows or []]
        self._scalar_rows = scalar_rows or []
        self._version = version
        self.execute_count = 0
        self.scalars_count = 0
        self.get_count = 0

    async def execute(self, statement: object) -> _Rows:
        del statement
        rows = self._batches[self.execute_count]
        self.execute_count += 1
        return _Rows(rows)

    async def scalars(self, statement: object) -> list[object]:
        del statement
        self.scalars_count += 1
        return self._scalar_rows

    async def get(self, model: object, identity: object) -> object | None:
        del model, identity
        self.get_count += 1
        return self._version


class _SessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _PageService(WikiService):
    def __init__(self, session: _FakeSession, page: object) -> None:
        super().__init__(lambda: _SessionContext(session))  # type: ignore[arg-type]
        self._fake_page = page

    async def _base(self, session: object, knowledge_base_id: UUID) -> object:
        del session, knowledge_base_id
        return SimpleNamespace()

    async def _page(
        self,
        session: object,
        knowledge_base_id: UUID,
        page_id: UUID,
        *,
        lock: bool = False,
    ) -> object:
        del session, knowledge_base_id, page_id, lock
        return self._fake_page


def test_build_source_excerpt_uses_parent_and_child_offsets() -> None:
    parent = _chunk(content="前文。必须启用两个独立认证因素。后文。", kind="parent")
    child = _chunk(
        content="必须启用两个独立认证因素。",
        parent_chunk_id=parent.id,
        char_start=3,
        char_end=15,
    )

    excerpt = build_source_excerpt(parent, child)

    assert excerpt.parent_chunk_id == parent.id
    assert parent.content[excerpt.focus_start : excerpt.focus_end] == child.content
    assert child.content in excerpt.excerpt


async def test_load_verified_claims_marks_stale_claims_and_sources_invalid() -> None:
    valid = _row()
    cases = [
        _row(page_current=False),
        _row(trust_state="needs_review"),
        _row(document_active=False),
        _row(source_kind="parent"),
        _row(source_ordinal=99),
        _row(source_hash="0" * 64),
    ]
    rows = [valid, *cases]
    session = _FakeSession(
        rows,
        [(item.parent,) for item in rows],
    )

    claims = await WikiReadModel(session).load_verified_claims(
        tuple(item.claim.id for item in rows),
        scope_ids=(valid.page.knowledge_base_id,),
        require_index_ready=False,
    )

    assert claims[0].valid is True
    assert [claim.valid for claim in claims[1:]] == [False, False, False, False, False, False]
    assert [claim.invalid_reason for claim in claims[1:3]] == [
        "page_version_not_current",
        "claim_not_verified",
    ]
    assert [claim.sources[0].invalid_reason for claim in claims[3:]] == [
        "document_version_not_active",
        "chunk_kind_mismatch",
        "chunk_ordinal_mismatch",
        "chunk_hash_mismatch",
    ]


async def test_load_verified_claims_uses_fixed_batches_for_many_claims() -> None:
    rows = [_row(claim_id=uuid4()) for _ in range(20)]
    session = _FakeSession(rows, [(item.parent,) for item in rows])

    claims = await WikiReadModel(session).load_verified_claims(
        tuple(item.claim.id for item in rows),
        scope_ids=tuple({item.page.knowledge_base_id for item in rows}),
        require_index_ready=True,
    )

    assert len(claims) == 20
    assert all(claim.valid for claim in claims)
    assert session.execute_count == 2


async def test_search_metadata_uses_exact_and_prefix_fields_without_similarity() -> None:
    exact_id = UUID("00000000-0000-0000-0000-000000000001")
    slug_id = UUID("00000000-0000-0000-0000-000000000002")
    title_prefix_id = UUID("00000000-0000-0000-0000-000000000003")
    alias_prefix_id = UUID("00000000-0000-0000-0000-000000000004")
    rows = [
        SimpleNamespace(
            page_id=alias_prefix_id, slug="topic/other", title="其他", alias="MFA 策略"
        ),
        SimpleNamespace(page_id=slug_id, slug="mfa-policy", title="策略", alias=None),
        SimpleNamespace(page_id=title_prefix_id, slug="topic/title", title="MFA 管理", alias=None),
        SimpleNamespace(page_id=exact_id, slug="topic/exact", title="mfa", alias=None),
    ]
    session = _FakeSession(rows)

    hits = await WikiReadModel(session).search_metadata("MFA", (uuid4(),), limit=10)

    assert [(hit.page_id, hit.exact, hit.matched_field) for hit in hits] == [
        (exact_id, True, "title"),
        (slug_id, False, "slug"),
        (title_prefix_id, False, "title"),
        (alias_prefix_id, False, "alias"),
    ]


async def test_get_page_uses_read_model_once_and_returns_source_excerpt_fields() -> None:
    row = _row()
    page = row.page
    version = SimpleNamespace(
        id=row.version.id,
        version_no=1,
        content="页面正文",
        summary="页面摘要",
        origin="pipeline",
        index_state="pending",
        index_error_code=None,
        generation_metadata={},
    )
    session = _FakeSession([row], [(row.parent,)], scalar_rows=[row.claim], version=version)
    service = _PageService(session, page)

    response = await service.get_page(page.knowledge_base_id, page.id)

    source = response["claims"][0]["sources"][0]
    assert session.scalars_count == 1
    assert session.execute_count == 2
    assert session.get_count == 1
    assert source["parent_chunk_id"] == row.parent.id
    assert source["excerpt"] == row.parent.content
    assert row.chunk.content == source["excerpt"][source["focus_start"] : source["focus_end"]]
    assert source["invalid_reason"] is None
