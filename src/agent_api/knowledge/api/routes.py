from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import StreamingResponse

from agent_api.knowledge.application.management import (
    KnowledgeConflictError,
    KnowledgeManagementError,
    KnowledgeNotFoundError,
)


class ManagementService(Protocol):
    max_upload_bytes: int

    async def create_knowledge_base(self, **values: object) -> Any: ...

    async def list_knowledge_bases(self) -> Any: ...

    async def get_knowledge_base(self, knowledge_base_id: UUID) -> Any: ...

    async def update_knowledge_base(self, knowledge_base_id: UUID, **values: object) -> Any: ...

    async def list_documents(self, knowledge_base_id: UUID) -> Any: ...

    async def get_document(self, document_id: UUID) -> Any: ...

    async def request_document_action(self, document_id: UUID, action: str) -> Any: ...

    async def delete_document(self, document_id: UUID) -> Any: ...

    def stream_events(self) -> AsyncIterator[dict[str, object]]: ...

    async def upload_document(
        self,
        knowledge_base_id: UUID,
        filename: str,
        mime_type: str,
        content: bytes,
        *,
        on_duplicate: str = "reject",
    ) -> Any: ...

    async def delete_knowledge_base(self, knowledge_base_id: UUID, *, confirm: bool) -> None: ...


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def strip_and_reject_blank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    enabled: bool
    document_count: int
    processing_count: int


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class DocumentAcceptedResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    status: str


class DocumentUploadAcceptedResponse(DocumentAcceptedResponse):
    document_version_id: UUID


class IngestionJobResponse(BaseModel):
    id: UUID
    stage: str
    status: str
    progress: int
    error_code: str | None = None
    error_reference: str | None = None


class DocumentResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    title: str
    filename: str
    mime_type: str
    status: str
    active_version_id: UUID | None
    job: IngestionJobResponse | None


def get_management_service(request: Request) -> ManagementService:
    service = getattr(request.app.state, "knowledge_management", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "knowledge_unavailable", "message": "Knowledge service unavailable"},
        )
    return service  # type: ignore[no-any-return]


ServiceDependency = Annotated[ManagementService, Depends(get_management_service)]

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


def _raise_http_error(error: KnowledgeManagementError) -> None:
    if isinstance(error, KnowledgeNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": error.code, "message": "Knowledge resource not found"},
        ) from None
    if isinstance(error, KnowledgeConflictError):
        detail: dict[str, object] = {
            "code": error.code,
            "message": (
                "Knowledge base contains documents"
                if error.code == "knowledge_base_not_empty"
                else "Knowledge resource conflict"
            ),
        }
        if error.requires_confirmation:
            detail["requires_confirmation"] = True
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from None
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": error.code, "message": "Knowledge request rejected"},
    ) from None


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    service: ServiceDependency,
) -> Any:
    try:
        return await service.create_knowledge_base(**payload.model_dump())
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(service: ServiceDependency) -> Any:
    return await service.list_knowledge_bases()


@router.get("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(knowledge_base_id: UUID, service: ServiceDependency) -> Any:
    try:
        return await service.get_knowledge_base(knowledge_base_id)
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeBaseUpdate,
    service: ServiceDependency,
) -> Any:
    try:
        return await service.update_knowledge_base(
            knowledge_base_id,
            **payload.model_dump(exclude_unset=True),
        )
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.delete("/knowledge-bases/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    service: ServiceDependency,
    confirm: Annotated[bool, Query()] = False,
) -> None:
    try:
        await service.delete_knowledge_base(knowledge_base_id, confirm=confirm)
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentUploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    knowledge_base_id: UUID,
    service: ServiceDependency,
    file: Annotated[UploadFile, File()],
    on_duplicate: Annotated[Literal["reject", "new_version"], Query()] = "reject",
) -> Any:
    try:
        # 限量读取，避免超大请求一次性占用无限内存。
        content = await file.read(service.max_upload_bytes + 1)
        if len(content) > service.max_upload_bytes:
            raise KnowledgeManagementError("file_too_large")
        return await service.upload_document(
            knowledge_base_id,
            file.filename or "upload",
            file.content_type or "application/octet-stream",
            content,
            on_duplicate=on_duplicate,
        )
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=list[DocumentResponse],
)
async def list_documents(knowledge_base_id: UUID, service: ServiceDependency) -> Any:
    try:
        return await service.list_documents(knowledge_base_id)
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID, service: ServiceDependency) -> Any:
    try:
        return await service.get_document(document_id)
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.post(
    "/documents/{document_id}/retry",
    response_model=DocumentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document(document_id: UUID, service: ServiceDependency) -> Any:
    try:
        return await service.request_document_action(document_id, "retry")
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.post(
    "/documents/{document_id}/reindex",
    response_model=DocumentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_document(document_id: UUID, service: ServiceDependency) -> Any:
    try:
        return await service.request_document_action(document_id, "reindex")
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.delete(
    "/documents/{document_id}",
    response_model=DocumentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_document(document_id: UUID, service: ServiceDependency) -> Any:
    try:
        return await service.delete_document(document_id)
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.get("/knowledge/events/stream")
async def stream_knowledge_events(service: ServiceDependency) -> StreamingResponse:
    async def encode_events() -> AsyncIterator[str]:
        async for event in service.stream_events():
            event_type = str(event.get("type", "progress"))
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        encode_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
