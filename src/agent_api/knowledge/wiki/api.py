from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from agent_api.knowledge.wiki.retrieval import WikiRetrievalError, WikiRetrievalService
from agent_api.knowledge.wiki.service import WikiError, WikiService


class WikiConfigUpdate(BaseModel):
    enabled: bool | None = None
    token_limit: int | None = Field(default=None, ge=0)


class WikiConfigResponse(BaseModel):
    knowledge_base_id: UUID
    enabled: bool
    token_limit: int
    tokens_reserved: int
    tokens_charged: int


class WikiPageSummary(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    slug: str
    page_type: Literal["summary", "topic", "index"]
    title: str
    current_version_id: UUID | None
    index_state: Literal["not_required", "pending", "indexing", "ready", "failed"] = "not_required"
    index_error_code: str | None = None


class WikiAggregationRebuildResponse(BaseModel):
    knowledge_base_id: UUID
    topic_pages: int
    summary_pages: int


class WikiSourceResponse(BaseModel):
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID | None
    document_title: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    valid: bool


class WikiClaimResponse(BaseModel):
    id: UUID
    claim_key: str
    text: str
    position: int
    claim_type: Literal["summary_fact", "topic_fact", "source_excerpt", "manual"]
    trust_state: Literal["verified", "needs_review"]
    reason: str | None
    sources: list[WikiSourceResponse]


class WikiSummarySectionResponse(BaseModel):
    id: UUID
    title: str
    text: str
    position: int
    trust_state: Literal["verified", "needs_review"]
    reason: str | None
    sources: list[WikiSourceResponse]


class WikiMetadataResponse(BaseModel):
    key: str
    label: str
    value: str


class WikiPageResponse(WikiPageSummary):
    version_no: int
    content: str
    summary: str
    origin: Literal["pipeline", "user", "revert"]
    summary_sections: list[WikiSummarySectionResponse]
    metadata: list[WikiMetadataResponse]
    claims: list[WikiClaimResponse]


class WikiVersionResponse(BaseModel):
    id: UUID
    page_id: UUID
    version_no: int
    title: str
    content: str
    summary: str
    origin: Literal["pipeline", "user", "revert"]
    review_state: Literal["published", "pending_review", "rejected", "superseded"]
    base_published_version_id: UUID | None
    created_at: datetime


class WikiEditRequest(BaseModel):
    base_version: UUID
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)


class WikiRevertRequest(BaseModel):
    base_version: UUID
    target_version_id: UUID


class WikiReviewRequest(BaseModel):
    candidate_version_id: UUID
    base_version: UUID
    decision: Literal["publish", "reject"]


class WikiJobResponse(BaseModel):
    id: UUID
    job_type: str
    status: str
    stage: str
    progress: int
    error_code: str | None
    error_reference: str | None = None
    page_version_id: UUID | None = None
    embedding_fingerprint: str | None = None
    estimated_tokens: int
    actual_tokens: int | None
    charged_tokens: int
    created_at: datetime


class WikiIndexRetryResponse(BaseModel):
    job_id: UUID
    page_version_id: UUID


class WikiIndexBackfillResponse(BaseModel):
    created_jobs: int
    existing_jobs: int
    skipped_versions: int


class WikiSearchSourceResponse(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    filename: str
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]
    excerpt: str
    score: float
    rank: int


class WikiMatchedClaimResponse(BaseModel):
    id: UUID
    text: str
    position: int
    claim_type: Literal["summary_fact", "topic_fact", "source_excerpt", "manual"]
    score: float
    sources: list[WikiSearchSourceResponse]


class WikiMatchedPageResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    slug: str
    title: str
    page_type: Literal["summary", "topic", "index"]
    score: float
    matched_fields: list[str]
    matched_claims: list[WikiMatchedClaimResponse]
    omitted_claim_count: int


class WikiSearchResponse(BaseModel):
    pages: list[WikiMatchedPageResponse]
    degraded: bool
    degradation_reason: str | None
    truncation_reason: str | None


def get_wiki_service(request: Request) -> WikiService:
    service = getattr(request.app.state, "wiki_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "wiki_unavailable", "message": "Wiki service unavailable"},
        )
    return service  # type: ignore[no-any-return]


def get_wiki_retrieval(request: Request) -> WikiRetrievalService:
    service = getattr(request.app.state, "wiki_retrieval", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "wiki_unavailable", "message": "Wiki search unavailable"},
        )
    return service  # type: ignore[no-any-return]


WikiServiceDependency = Annotated[WikiService, Depends(get_wiki_service)]
WikiRetrievalDependency = Annotated[WikiRetrievalService, Depends(get_wiki_retrieval)]
router = APIRouter(prefix="/api/v1/knowledge-bases/{knowledge_base_id}/wiki", tags=["wiki"])


def _raise_http_error(error: WikiError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": "Wiki request rejected"},
    ) from None


@router.get("/config", response_model=WikiConfigResponse)
async def get_config(knowledge_base_id: UUID, service: WikiServiceDependency) -> Any:
    try:
        return await service.get_config(knowledge_base_id)
    except WikiError as error:
        _raise_http_error(error)


@router.patch("/config", response_model=WikiConfigResponse)
async def update_config(
    knowledge_base_id: UUID,
    payload: WikiConfigUpdate,
    response: Response,
    service: WikiServiceDependency,
) -> Any:
    try:
        result = await service.update_config(
            knowledge_base_id, enabled=payload.enabled, token_limit=payload.token_limit
        )
        if payload.enabled is True:
            response.status_code = 202
        return result
    except WikiError as error:
        _raise_http_error(error)


@router.get("/pages", response_model=list[WikiPageSummary])
async def list_pages(
    knowledge_base_id: UUID,
    service: WikiServiceDependency,
    page_type: Literal["summary", "topic", "index"] | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> Any:
    try:
        return await service.list_pages(
            knowledge_base_id, page_type=page_type, cursor=cursor, limit=limit
        )
    except WikiError as error:
        _raise_http_error(error)


@router.post("/rebuild-aggregation", response_model=WikiAggregationRebuildResponse)
async def rebuild_aggregation(knowledge_base_id: UUID, service: WikiServiceDependency) -> Any:
    try:
        return await service.rebuild_aggregation_pages(knowledge_base_id)
    except WikiError as error:
        _raise_http_error(error)


@router.get("/pages/{page_id}", response_model=WikiPageResponse)
async def get_page(knowledge_base_id: UUID, page_id: UUID, service: WikiServiceDependency) -> Any:
    try:
        return await service.get_page(knowledge_base_id, page_id)
    except WikiError as error:
        _raise_http_error(error)


@router.get("/pages/{page_id}/versions", response_model=list[WikiVersionResponse])
async def list_versions(
    knowledge_base_id: UUID, page_id: UUID, service: WikiServiceDependency
) -> Any:
    try:
        return await service.list_versions(knowledge_base_id, page_id)
    except WikiError as error:
        _raise_http_error(error)


@router.put("/pages/{page_id}", response_model=WikiPageResponse)
async def edit_page(
    knowledge_base_id: UUID,
    page_id: UUID,
    payload: WikiEditRequest,
    service: WikiServiceDependency,
) -> Any:
    try:
        return await service.edit_page(
            knowledge_base_id,
            page_id,
            base_version=payload.base_version,
            title=payload.title.strip(),
            content=payload.content,
        )
    except WikiError as error:
        _raise_http_error(error)


@router.post("/pages/{page_id}/revert", response_model=WikiPageResponse)
async def revert_page(
    knowledge_base_id: UUID,
    page_id: UUID,
    payload: WikiRevertRequest,
    service: WikiServiceDependency,
) -> Any:
    try:
        return await service.revert_page(
            knowledge_base_id,
            page_id,
            base_version=payload.base_version,
            target_version=payload.target_version_id,
        )
    except WikiError as error:
        _raise_http_error(error)


@router.post("/pages/{page_id}/review", response_model=WikiVersionResponse)
async def review_page(
    knowledge_base_id: UUID,
    page_id: UUID,
    payload: WikiReviewRequest,
    service: WikiServiceDependency,
) -> Any:
    try:
        return await service.review_page(
            knowledge_base_id,
            page_id,
            candidate_version_id=payload.candidate_version_id,
            base_version=payload.base_version,
            decision=payload.decision,
        )
    except WikiError as error:
        _raise_http_error(error)


@router.post(
    "/pages/{page_id}/index/retry",
    response_model=WikiIndexRetryResponse,
    status_code=202,
)
async def retry_page_index(
    knowledge_base_id: UUID,
    page_id: UUID,
    service: WikiServiceDependency,
) -> Any:
    try:
        return await service.retry_page_index(knowledge_base_id, page_id)
    except WikiError as error:
        _raise_http_error(error)


@router.post(
    "/index/backfill",
    response_model=WikiIndexBackfillResponse,
    status_code=202,
)
async def backfill_index(
    knowledge_base_id: UUID,
    service: WikiServiceDependency,
) -> Any:
    try:
        return await service.backfill_index(knowledge_base_id)
    except WikiError as error:
        _raise_http_error(error)


@router.get("/jobs", response_model=list[WikiJobResponse])
async def list_jobs(
    knowledge_base_id: UUID,
    service: WikiServiceDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> Any:
    try:
        return await service.list_jobs(knowledge_base_id, limit=limit)
    except WikiError as error:
        _raise_http_error(error)


@router.get("/search", response_model=WikiSearchResponse)
async def search_pages(
    knowledge_base_id: UUID,
    service: WikiRetrievalDependency,
    q: str = Query(min_length=1, max_length=200),
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    limit: int = Query(default=5, ge=1, le=20),
    claim_limit: int = Query(default=12, ge=1, le=50),
) -> Any:
    try:
        return await service.search(
            q,
            (knowledge_base_id,),
            mode=mode,
            page_limit=limit,
            claim_limit=claim_limit,
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_wiki_search", "message": "Invalid Wiki search"},
        ) from None
    except WikiRetrievalError:
        raise HTTPException(
            status_code=503,
            detail={"code": "wiki_retrieval_unavailable", "message": "Wiki search unavailable"},
        ) from None
