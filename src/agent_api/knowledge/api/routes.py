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
    # 知识库管理服务协议：路由层只依赖这些方法，便于测试时替换实现。
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
    # 创建知识库的请求体。
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
    # 知识库列表和详情接口统一返回的结构，包含文档统计与处理中数量。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    enabled: bool
    document_count: int
    processing_count: int


class KnowledgeBaseUpdate(BaseModel):
    # 更新知识库的请求体；字段都可选，只更新调用方实际传入的字段。
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
    # 文档异步任务已受理时返回的基础结构。
    document_id: UUID
    job_id: UUID
    status: str


class DocumentUploadAcceptedResponse(DocumentAcceptedResponse):
    # 上传文档成功受理时额外返回本次入库的文档版本 ID。
    document_version_id: UUID


class IngestionJobResponse(BaseModel):
    # 文档解析、切分、向量化等入库任务的当前进度。
    id: UUID
    stage: str
    status: str
    progress: int
    error_code: str | None = None
    error_reference: str | None = None


class DocumentResponse(BaseModel):
    # 文档详情结构，包含所属知识库、当前状态、激活版本和最近任务。
    id: UUID
    knowledge_base_id: UUID
    title: str
    filename: str
    mime_type: str
    status: str
    active_version_id: UUID | None
    job: IngestionJobResponse | None


def get_management_service(request: Request) -> ManagementService:
    # 从 FastAPI 应用状态中取知识库管理服务；未启用知识库功能时返回 503。
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
    # 将应用层的知识库异常统一转换成稳定的 HTTP 错误响应。
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
    """创建一个新的知识库，用于后续上传文档并作为检索范围。"""
    try:
        return await service.create_knowledge_base(**payload.model_dump())
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(service: ServiceDependency) -> Any:
    """查询当前工作区下的全部知识库，供前端列表和知识范围选择器使用。"""
    return await service.list_knowledge_bases()


@router.get("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(knowledge_base_id: UUID, service: ServiceDependency) -> Any:
    """查询单个知识库的详情，包括启用状态和文档处理统计。"""
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
    """修改知识库名称、描述或启用状态。"""
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
    """删除知识库；包含文档时需要调用方通过 confirm=true 二次确认。"""
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
    """上传文档到指定知识库，并创建异步入库任务。"""
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
    """查询指定知识库下的文档列表和每个文档最近一次入库任务状态。"""
    try:
        return await service.list_documents(knowledge_base_id)
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID, service: ServiceDependency) -> Any:
    """查询单个文档详情，通常用于查看上传、解析或索引状态。"""
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
    """对失败的文档入库任务发起重试，重新进入解析和索引流程。"""
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
    """对已就绪的文档重新建立索引，常用于向量库或切分策略变更后的重建。"""
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
    """异步删除文档及其版本、切片、向量索引等关联资源。"""
    try:
        return await service.delete_document(document_id)
    except KnowledgeManagementError as error:
        _raise_http_error(error)


@router.get("/knowledge/events/stream")
async def stream_knowledge_events(service: ServiceDependency) -> StreamingResponse:
    """订阅知识库后台任务事件流，前端用它实时刷新处理进度。"""
    async def encode_events() -> AsyncIterator[str]:
        # 按 Server-Sent Events 格式编码，event 字段用于区分进度、完成或失败事件。
        async for event in service.stream_events():
            event_type = str(event.get("type", "progress"))
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        encode_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
