from __future__ import annotations

import os
from collections.abc import Iterable
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, update
from sqlalchemy.dialects import postgresql

from agent_api.knowledge.application.management import DEFAULT_WORKSPACE_ID
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeBase,
    WikiClaim,
    WikiClaimSource,
    WikiConfig,
    WikiPage,
    WikiPageAlias,
    WikiPageVersion,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.wiki.read_model import (
    WikiReadModel,
    _metadata_statement,
    build_source_excerpt,
)
from agent_api.knowledge.wiki.service import WikiService

DATABASE_URL = "postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent"


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
    document_deleted: bool = False,
    source_kind: str = "child",
    source_ordinal: int = 1,
    source_hash: str | None = None,
    chunk_kind: str = "child",
    chunk_ordinal: int = 1,
    chunk_content: str = "必须启用两个独立认证因素。",
    parent_content: str = "前文。必须启用两个独立认证因素。后文。",
    document_scope_matches: bool = True,
) -> SimpleNamespace:
    page_id = uuid4()
    knowledge_base_id = uuid4()
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
        deleted_at=object() if document_deleted else None,
        active_version_id=document_version_id if document_active else uuid4(),
        knowledge_base_id=knowledge_base_id if document_scope_matches else uuid4(),
    )
    return SimpleNamespace(
        page=SimpleNamespace(
            id=page_id,
            knowledge_base_id=knowledge_base_id,
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


async def test_load_verified_claims_marks_cross_scope_document_invalid_for_page_display() -> None:
    external_text = "外部知识库的机密认证流程。"
    row = _row(
        document_scope_matches=False,
        chunk_content=external_text,
        parent_content=f"前文。{external_text}后文。",
    )
    session = _FakeSession([row], [(row.parent,)])

    claims = await WikiReadModel(session).load_verified_claims(
        (row.claim.id,),
        scope_ids=(row.page.knowledge_base_id,),
        require_index_ready=False,
    )

    assert claims[0].valid is False
    assert claims[0].invalid_reason == "document_scope_mismatch"
    assert claims[0].sources[0].valid is False
    assert claims[0].sources[0].invalid_reason == "document_scope_mismatch"
    assert claims[0].sources[0].excerpt == ""
    assert claims[0].sources[0].parent_chunk_id is None
    assert claims[0].sources[0].focus_start is None
    assert claims[0].sources[0].focus_end is None
    assert claims[0].sources[0].document_title == ""
    assert claims[0].sources[0].filename == ""
    assert claims[0].sources[0].heading_path == ()
    assert claims[0].sources[0].page_start is None
    assert claims[0].sources[0].page_end is None


@pytest.mark.parametrize(
    ("document_ready", "document_deleted"),
    [(False, False), (True, True)],
)
async def test_load_verified_claims_redacts_cross_scope_unready_document_sources(
    document_ready: bool,
    document_deleted: bool,
) -> None:
    external_text = "外部知识库的未就绪机密来源。"
    row = _row(
        document_scope_matches=False,
        document_ready=document_ready,
        document_deleted=document_deleted,
        chunk_content=external_text,
        parent_content=f"前文。{external_text}后文。",
    )
    session = _FakeSession([row], [(row.parent,)])

    claims = await WikiReadModel(session).load_verified_claims(
        (row.claim.id,),
        scope_ids=(row.page.knowledge_base_id,),
        require_index_ready=False,
    )

    source = claims[0].sources[0]
    assert claims[0].valid is False
    assert claims[0].invalid_reason == "document_scope_mismatch"
    assert source.invalid_reason == "document_scope_mismatch"
    assert source.excerpt == ""
    assert source.parent_chunk_id is None
    assert source.focus_start is None
    assert source.focus_end is None
    assert source.document_title == ""
    assert source.filename == ""
    assert source.heading_path == ()
    assert source.page_start is None
    assert source.page_end is None


async def test_load_verified_claims_redacts_orphan_document_sources() -> None:
    orphan_text = "孤儿来源中的机密正文。"
    row = _row(
        chunk_content=orphan_text,
        parent_content=f"前文。{orphan_text}后文。",
    )
    row.document = None
    session = _FakeSession([row], [(row.parent,)])

    claims = await WikiReadModel(session).load_verified_claims(
        (row.claim.id,),
        scope_ids=(row.page.knowledge_base_id,),
        require_index_ready=False,
    )

    source = claims[0].sources[0]
    assert claims[0].valid is False
    assert claims[0].invalid_reason == "document_not_ready"
    assert source.invalid_reason == "document_not_ready"
    assert source.excerpt == ""
    assert source.parent_chunk_id is None
    assert source.focus_start is None
    assert source.focus_end is None
    assert source.document_title == ""
    assert source.filename == ""
    assert source.heading_path == ()
    assert source.page_start is None
    assert source.page_end is None


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


def test_metadata_statement_does_not_filter_title_or_alias_before_normalization() -> None:
    statement = _metadata_statement("mfa control", (uuid4(),), limit=10)
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "lower(wiki_pages.title) LIKE" not in sql
    assert "lower(wiki_page_aliases.alias) LIKE" not in sql


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


@pytest.mark.knowledge_integration
@pytest.mark.skipif(
    os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1",
    reason="set RUN_KNOWLEDGE_INTEGRATION=1 to use local knowledge containers",
)
async def test_search_metadata_matches_normalized_title_and_alias_prefixes() -> None:
    runtime = DatabaseRuntime(DATABASE_URL)
    knowledge_base_id = uuid4()
    title_page_id = uuid4()
    alias_page_id = uuid4()
    if not await _database_available(runtime):
        await runtime.close()
        pytest.skip("local knowledge PostgreSQL is not available")
    try:
        async with runtime.session_factory() as session, session.begin():
            session.add(
                KnowledgeBase(
                    id=knowledge_base_id,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    name=f"Wiki read model metadata {knowledge_base_id}",
                    description="",
                    enabled=True,
                )
            )
            session.add(WikiConfig(knowledge_base_id=knowledge_base_id, enabled=True))
            title_page = WikiPage(
                id=title_page_id,
                knowledge_base_id=knowledge_base_id,
                slug="topic/normalized-title",
                page_type="topic",
                title="ＭＦＡ　　Control 策略",
            )
            alias_page = WikiPage(
                id=alias_page_id,
                knowledge_base_id=knowledge_base_id,
                slug="topic/normalized-alias",
                page_type="topic",
                title="其他策略",
            )
            session.add_all((title_page, alias_page))
            await session.flush()
            title_version = WikiPageVersion(
                page_id=title_page_id,
                version_no=1,
                title=title_page.title,
                content="title",
                summary="",
                generation_metadata={},
                origin="pipeline",
                review_state="published",
                index_state="ready",
            )
            alias_version = WikiPageVersion(
                page_id=alias_page_id,
                version_no=1,
                title=alias_page.title,
                content="alias",
                summary="",
                generation_metadata={},
                origin="pipeline",
                review_state="published",
                index_state="ready",
            )
            session.add_all((title_version, alias_version))
            await session.flush()
            title_page.current_version_id = title_version.id
            alias_page.current_version_id = alias_version.id
            session.add(
                WikiPageAlias(
                    page_id=alias_page_id,
                    alias="ＭＦＡ　　Alias 策略",
                    normalized_alias="mfa alias 策略",
                )
            )

        async with runtime.session_factory() as session:
            hits = await WikiReadModel(session).search_metadata(
                "mfa control",
                (knowledge_base_id,),
                limit=10,
            )
            alias_hits = await WikiReadModel(session).search_metadata(
                "mfa alias",
                (knowledge_base_id,),
                limit=10,
            )

        assert [(hit.page_id, hit.matched_field) for hit in hits] == [(title_page_id, "title")]
        assert [(hit.page_id, hit.matched_field) for hit in alias_hits] == [
            (alias_page_id, "alias")
        ]
    finally:
        await _cleanup_wiki_read_model_base(runtime, knowledge_base_id)
        await runtime.close()


@pytest.mark.knowledge_integration
@pytest.mark.skipif(
    os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1",
    reason="set RUN_KNOWLEDGE_INTEGRATION=1 to use local knowledge containers",
)
async def test_load_verified_claims_uses_real_join_filters_and_fixed_query_count() -> None:
    runtime = DatabaseRuntime(DATABASE_URL)
    knowledge_base_id = uuid4()
    out_of_scope_id = uuid4()
    cross_source_id = uuid4()
    if not await _database_available(runtime):
        await runtime.close()
        pytest.skip("local knowledge PostgreSQL is not available")
    try:
        async with runtime.session_factory() as session, session.begin():
            claim_ids = await _seed_verified_claims_fixture(
                session,
                knowledge_base_id,
                cross_source_knowledge_base_id=cross_source_id,
            )
            out_of_scope_claim_id = await _seed_out_of_scope_claim(session, out_of_scope_id)

        statements: list[str] = []

        def count_selects(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            del conn, cursor, parameters, context, executemany
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(runtime._engine.sync_engine, "before_cursor_execute", count_selects)
        try:
            async with runtime.session_factory() as session:
                claims = await WikiReadModel(session).load_verified_claims(
                    (*claim_ids, out_of_scope_claim_id),
                    scope_ids=(knowledge_base_id,),
                    require_index_ready=True,
                )
        finally:
            event.remove(runtime._engine.sync_engine, "before_cursor_execute", count_selects)

        by_key = {claim.claim_key: claim for claim in claims}
        valid_claims = [claim for claim in claims if claim.valid]

        assert len(valid_claims) == 20
        assert len(claims) == len(claim_ids)
        assert out_of_scope_claim_id not in {claim.claim_id for claim in claims}
        assert by_key["old-page-version"].invalid_reason == "page_version_not_current"
        assert by_key["needs-review"].invalid_reason == "claim_not_verified"
        assert by_key["inactive-document-version"].sources[0].invalid_reason == (
            "document_version_not_active"
        )
        assert by_key["kind-mismatch"].sources[0].invalid_reason == "chunk_kind_mismatch"
        assert by_key["ordinal-mismatch"].sources[0].invalid_reason == "chunk_ordinal_mismatch"
        assert by_key["hash-mismatch"].sources[0].invalid_reason == "chunk_hash_mismatch"
        assert by_key["cross-scope-document"].sources[0].invalid_reason == (
            "document_scope_mismatch"
        )
        assert len(statements) == 2
    finally:
        await _cleanup_wiki_read_model_base(runtime, knowledge_base_id)
        await _cleanup_wiki_read_model_base(runtime, out_of_scope_id)
        await _cleanup_wiki_read_model_base(runtime, cross_source_id)
        await runtime.close()


async def _database_available(runtime: DatabaseRuntime) -> bool:
    try:
        async with runtime._engine.connect():
            return True
    except OSError:
        return False


async def _cleanup_wiki_read_model_base(
    runtime: DatabaseRuntime,
    knowledge_base_id: UUID,
) -> None:
    async with runtime.session_factory() as session, session.begin():
        await session.execute(
            update(WikiPage)
            .where(WikiPage.knowledge_base_id == knowledge_base_id)
            .values(current_version_id=None)
        )
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id))


async def _seed_verified_claims_fixture(
    session: object,
    knowledge_base_id: UUID,
    *,
    cross_source_knowledge_base_id: UUID | None = None,
) -> tuple[UUID, ...]:
    page_id = uuid4()
    old_version_id = uuid4()
    document_id = uuid4()
    active_version_id = uuid4()
    stale_version_id = uuid4()
    parent_id = uuid4()
    child_id = uuid4()
    stale_child_id = uuid4()
    source_text = "必须启用两个独立认证因素。"
    source_hash = sha256(source_text.encode("utf-8")).hexdigest()
    cross_document_id = uuid4()
    cross_version_id = uuid4()
    cross_child_id = uuid4()
    session.add(
        KnowledgeBase(
            id=knowledge_base_id,
            workspace_id=DEFAULT_WORKSPACE_ID,
            name=f"Wiki read model claims {knowledge_base_id}",
            description="",
            enabled=True,
        )
    )
    session.add(WikiConfig(knowledge_base_id=knowledge_base_id, enabled=True))
    if cross_source_knowledge_base_id is not None:
        session.add(
            KnowledgeBase(
                id=cross_source_knowledge_base_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                name=f"Wiki read model cross source {cross_source_knowledge_base_id}",
                description="",
                enabled=True,
            )
        )
    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        title="认证规范",
        filename="auth.md",
        mime_type="text/markdown",
        status="ready",
    )
    session.add(document)
    await session.flush()
    if cross_source_knowledge_base_id is not None:
        cross_document = Document(
            id=cross_document_id,
            knowledge_base_id=cross_source_knowledge_base_id,
            title="外部认证规范",
            filename="external-auth.md",
            mime_type="text/markdown",
            status="ready",
        )
        session.add(cross_document)
        await session.flush()
        session.add(
            DocumentVersion(
                id=cross_version_id,
                document_id=cross_document_id,
                version_no=1,
                file_path="external-auth-v1.md",
                sha256="3" * 64,
                file_size=100,
                pipeline_version="test",
                embedding_provider="test",
                embedding_model="test",
                embedding_dimension=3,
                embedding_fingerprint="test",
                status="ready",
            )
        )
        await session.flush()
        cross_document.active_version_id = cross_version_id
    session.add_all(
        (
            DocumentVersion(
                id=active_version_id,
                document_id=document_id,
                version_no=1,
                file_path="auth-v1.md",
                sha256="1" * 64,
                file_size=100,
                pipeline_version="test",
                embedding_provider="test",
                embedding_model="test",
                embedding_dimension=3,
                embedding_fingerprint="test",
                status="ready",
            ),
            DocumentVersion(
                id=stale_version_id,
                document_id=document_id,
                version_no=2,
                file_path="auth-v2.md",
                sha256="2" * 64,
                file_size=100,
                pipeline_version="test",
                embedding_provider="test",
                embedding_model="test",
                embedding_dimension=3,
                embedding_fingerprint="test",
                status="ready",
            ),
        )
    )
    await session.flush()
    document.active_version_id = active_version_id
    cross_child = (
        Chunk(
            id=cross_child_id,
            document_version_id=cross_version_id,
            ordinal=1,
            kind="child",
            content=source_text,
            token_count=10,
        )
        if cross_source_knowledge_base_id is not None
        else None
    )
    parent = Chunk(
        id=parent_id,
        document_version_id=active_version_id,
        ordinal=0,
        kind="parent",
        content=f"前文。{source_text}后文。",
        token_count=20,
    )
    child = Chunk(
        id=child_id,
        document_version_id=active_version_id,
        parent_chunk_id=parent_id,
        ordinal=1,
        kind="child",
        content=source_text,
        char_start=3,
        char_end=3 + len(source_text),
        token_count=10,
    )
    stale_child = Chunk(
        id=stale_child_id,
        document_version_id=stale_version_id,
        ordinal=1,
        kind="child",
        content=source_text,
        token_count=10,
    )
    chunks = [parent, child, stale_child]
    if cross_child is not None:
        chunks.append(cross_child)
    session.add_all(chunks)
    page = WikiPage(
        id=page_id,
        knowledge_base_id=knowledge_base_id,
        slug="topic/auth",
        page_type="topic",
        title="身份认证",
    )
    session.add(page)
    await session.flush()
    current_version = WikiPageVersion(
        page_id=page_id,
        version_no=2,
        title="身份认证",
        content="current",
        summary="",
        generation_metadata={},
        origin="pipeline",
        review_state="published",
        index_state="ready",
    )
    old_version = WikiPageVersion(
        id=old_version_id,
        page_id=page_id,
        version_no=1,
        title="身份认证旧版",
        content="old",
        summary="",
        generation_metadata={},
        origin="pipeline",
        review_state="published",
        index_state="ready",
    )
    session.add_all((old_version, current_version))
    await session.flush()
    page.current_version_id = current_version.id
    claim_ids: list[UUID] = []
    for index in range(20):
        claim_ids.append(
            await _add_claim_with_source(
                session,
                page_version_id=current_version.id,
                claim_key=f"valid-{index:02d}",
                document_id=document_id,
                document_version_id=active_version_id,
                chunk_id=child_id,
                content_sha256=source_hash,
            )
        )
    invalid_specs = [
        (
            "old-page-version",
            old_version_id,
            "verified",
            active_version_id,
            child_id,
            "child",
            1,
            source_hash,
        ),
        (
            "needs-review",
            current_version.id,
            "needs_review",
            active_version_id,
            child_id,
            "child",
            1,
            source_hash,
        ),
        (
            "inactive-document-version",
            current_version.id,
            "verified",
            stale_version_id,
            stale_child_id,
            "child",
            1,
            source_hash,
        ),
        (
            "kind-mismatch",
            current_version.id,
            "verified",
            active_version_id,
            child_id,
            "parent",
            1,
            source_hash,
        ),
        (
            "ordinal-mismatch",
            current_version.id,
            "verified",
            active_version_id,
            child_id,
            "child",
            99,
            source_hash,
        ),
        (
            "hash-mismatch",
            current_version.id,
            "verified",
            active_version_id,
            child_id,
            "child",
            1,
            "0" * 64,
        ),
    ]
    for (
        claim_key,
        page_version_id,
        trust_state,
        document_version_id,
        chunk_id,
        chunk_kind,
        chunk_ordinal,
        content_sha,
    ) in invalid_specs:
        claim_ids.append(
            await _add_claim_with_source(
                session,
                page_version_id=page_version_id,
                claim_key=claim_key,
                document_id=document_id,
                document_version_id=document_version_id,
                chunk_id=chunk_id,
                chunk_kind=chunk_kind,
                chunk_ordinal=chunk_ordinal,
                content_sha256=content_sha,
                trust_state=trust_state,
            )
        )
    if cross_source_knowledge_base_id is not None:
        claim_ids.append(
            await _add_claim_with_source(
                session,
                page_version_id=current_version.id,
                claim_key="cross-scope-document",
                document_id=cross_document_id,
                document_version_id=cross_version_id,
                chunk_id=cross_child_id,
                content_sha256=source_hash,
            )
        )
    return tuple(claim_ids)


async def _seed_out_of_scope_claim(session: object, knowledge_base_id: UUID) -> UUID:
    claim_ids = await _seed_verified_claims_fixture(session, knowledge_base_id)
    return claim_ids[0]


async def _add_claim_with_source(
    session: object,
    *,
    page_version_id: UUID,
    claim_key: str,
    document_id: UUID,
    document_version_id: UUID,
    chunk_id: UUID,
    content_sha256: str,
    trust_state: str = "verified",
    chunk_kind: str = "child",
    chunk_ordinal: int = 1,
) -> UUID:
    claim = WikiClaim(
        page_version_id=page_version_id,
        claim_key=claim_key,
        text=f"{claim_key} 必须启用 MFA",
        trust_state=trust_state,
        reason=None,
        position=0,
        claim_type="summary_fact",
    )
    session.add(claim)
    await session.flush()
    session.add(
        WikiClaimSource(
            claim_id=claim.id,
            document_id=document_id,
            document_version_id=document_version_id,
            chunk_id=chunk_id,
            chunk_kind=chunk_kind,
            chunk_ordinal=chunk_ordinal,
            content_sha256=content_sha256,
            document_title="认证规范",
            heading_path=["认证"],
            page_start=1,
            page_end=1,
        )
    )
    return claim.id
