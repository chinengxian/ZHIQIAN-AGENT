from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agent_api.knowledge.domain.statuses import (
    ChunkKind,
    DocumentStatus,
    IngestionJobStatus,
    OutboxStatus,
    VersionStatus,
)

# PostgreSQL 是知识状态、正文、版本和引用的事实源；Milvus 只保存可重建索引。


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """为业务表提供带时区的 UTC 创建/更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        comment="记录创建时间，统一使用带时区的 UTC 时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
        comment="记录最后更新时间，统一使用带时区的 UTC 时间。",
    )


class Workspace(TimestampMixin, Base):
    """工作区隔离边界；首版由迁移创建一个固定工作区。"""

    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="工作区主键；当前版本使用迁移创建的固定默认工作区。",
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment="工作区名称，全局唯一。",
    )


class KnowledgeBase(TimestampMixin, Base):
    """知识库及其分块、检索策略配置。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_knowledge_bases_workspace_name"),
        Index("ix_knowledge_bases_workspace_enabled", "workspace_id", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="知识库主键。",
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属工作区 ID，关联 workspaces.id；工作区删除时级联删除知识库。",
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="知识库名称；同一工作区内不能重复。",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="知识库描述，用于前端展示和人工识别。",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用；聊天检索只会使用启用的知识库。",
    )
    chunking_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="知识库级分块配置预留字段。",
    )
    retrieval_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="知识库级检索配置预留字段。",
    )


class Document(TimestampMixin, Base):
    """面向用户的文档记录。

    ``active_version_id`` 是在线检索的唯一版本指针。新版本只有在解析、
    Embedding、Milvus 写入和完整性检查都成功后才能原子切换到这里。
    """

    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_kb_status", "knowledge_base_id", "status"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="文档主键，代表用户视角的一份文档。",
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属知识库 ID，关联 knowledge_bases.id；知识库删除时级联删除文档。",
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="文档标题，默认来自上传文件名去掉扩展名后的名称。",
    )
    filename: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="上传文件名的安全展示值，只保留末段，不作为磁盘路径使用。",
    )
    mime_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="文件 MIME 类型，用于记录上传时通过校验的媒体类型。",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DocumentStatus.PENDING.value,
        comment="文档用户态状态：pending、processing、ready、failed、deleting、deleted。",
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "document_versions.id",
            name="fk_documents_active_version_id",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
        comment="当前在线可检索版本 ID，关联 document_versions.id；新版本成功激活后才切换。",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="软删除时间；为空表示文档仍可见。",
    )


class DocumentVersion(Base):
    """不可变的文档处理版本，记录解析器和 Embedding 指纹以便重建。"""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_document_versions_number"),
        Index("ix_document_versions_document_status", "document_id", "status"),
        Index("ix_document_versions_sha256", "sha256"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="文档版本主键。",
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属文档 ID，关联 documents.id；文档删除时级联删除版本。",
    )
    version_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="文档内递增版本号；与 document_id 组成唯一约束。",
    )
    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="原文件在受控本地存储根目录下的相对文件名。",
    )
    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="上传文件内容 SHA-256，用于重复文件检测和审计。",
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="上传文件大小，单位为字节。",
    )
    parser_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="解析器名称，用于记录该版本由哪个解析器处理。",
    )
    parser_version: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="解析器版本，用于复现和排查解析差异。",
    )
    pipeline_version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="入库流水线版本，用于判断处理逻辑来源。",
    )
    embedding_provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Embedding 服务提供方，例如 openai。",
    )
    embedding_model: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Embedding 模型名称。",
    )
    embedding_dimension: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Embedding 向量维度，必须与 Milvus collection 维度一致。",
    )
    embedding_fingerprint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Embedding 配置指纹，用于判断重建索引时的配置来源。",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=VersionStatus.PENDING.value,
        comment="版本处理状态：pending、parsing、chunking、embedding、indexing、ready、failed。",
    )
    error_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="版本处理失败时的稳定错误码。",
    )
    error_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="版本处理失败参考号，用于排查且避免暴露内部异常。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        comment="版本创建时间，统一使用带时区的 UTC 时间。",
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="版本成功激活为 active_version 的时间。",
    )


class Chunk(Base):
    """PostgreSQL 中保存的父子块正文及来源定位信息。"""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "ordinal",
            "kind",
            name="uq_chunks_version_ordinal_kind",
        ),
        Index("ix_chunks_version_parent", "document_version_id", "parent_chunk_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="分块主键；Milvus 中的 chunk_id 与该字段对应。",
    )
    document_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属文档版本 ID，关联 document_versions.id；版本删除时级联删除分块。",
    )
    parent_chunk_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=True,
        comment="父块 ID，自关联 chunks.id；子块命中后通过该字段回到父块上下文。",
    )
    ordinal: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="当前文档版本内的分块顺序号。",
    )
    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ChunkKind.CHILD.value,
        comment="分块类型：parent 保存上下文，child 进入 Milvus 参与召回。",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="分块正文；PostgreSQL 是正文事实源。",
    )
    embedding_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="用于生成向量的文本，子块可能拼接标题路径，父块通常为空。",
    )
    heading_path: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="来源标题层级路径，用于引用展示和上下文增强。",
    )
    page_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="来源起始页码，主要用于 PDF 引用定位。",
    )
    page_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="来源结束页码，主要用于 PDF 引用定位。",
    )
    char_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="分块在父级内容中的起始字符位置。",
    )
    char_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="分块在父级内容中的结束字符位置。",
    )
    token_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="分块估算 token 数，用于预算和检索上下文控制。",
    )


class IngestionJob(TimestampMixin, Base):
    """异步入库任务及恢复所需的心跳、重试和幂等信息。"""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (Index("ix_ingestion_jobs_status_heartbeat", "status", "heartbeat_at"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="入库任务主键。",
    )
    document_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        comment="待处理文档版本 ID，关联 document_versions.id；版本删除时级联删除任务。",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=IngestionJobStatus.PENDING.value,
        comment="任务状态：pending、running、retry_wait、succeeded、failed。",
    )
    current_stage: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        comment="当前处理阶段，如 parsing、chunking、embedding、indexing、ready、deleting。",
    )
    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="任务进度百分比，前端进度条读取该字段。",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="任务已经尝试执行的次数。",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        comment="任务最大尝试次数。",
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Worker 最近一次心跳时间，用于恢复卡死任务。",
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="任务下次可重试时间；retry_wait 状态下用于延迟恢复。",
    )
    error_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="任务失败时的稳定错误码。",
    )
    error_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="任务失败参考号，用于排查且避免暴露内部异常。",
    )
    correlation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
        comment="任务链路追踪 ID，用于串起同一次异步处理。",
    )
    idempotency_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment="任务幂等键，避免重复上传、重试或删除产生重复任务。",
    )


class OutboxEvent(Base):
    """与业务数据同事务写入的待投递事件。

    Dispatcher 后续将 pending 事件投递到 Redis/Celery；即使队列短暂不可用，
    已提交的文档任务仍可从本表恢复。
    """

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_status_available", "status", "available_at"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="Outbox 事件主键。",
    )
    event_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment="事件幂等键，确保同一业务事件只进入 outbox 一次。",
    )
    event_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="由 OutboxEventType 约束的知识模块异步事件类型。",
    )
    aggregate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="业务聚合 ID，按 event_type 逻辑关联到文档版本、文档或知识库，不建外键。",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="事件载荷，只保存稳定 ID 和必要上下文，不保存正文和密钥。",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=OutboxStatus.PENDING.value,
        comment="投递状态：pending、processing、published、failed。",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Outbox 投递尝试次数。",
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        comment="事件可投递时间，用于失败后的指数退避和延迟任务。",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="事件成功投递到队列的时间。",
    )
    last_error_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="最近一次投递失败参考号。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        comment="事件创建时间，统一使用带时区的 UTC 时间。",
    )


class WikiConfig(TimestampMixin, Base):
    """知识库内 Wiki 的开关与累计生成额度。"""

    __tablename__ = "wiki_configs"

    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_charged: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    generation_model_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)


class WikiPage(TimestampMixin, Base):
    """页面稳定身份；正文只保存在版本表。"""

    __tablename__ = "wiki_pages"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "slug", name="uq_wiki_page_kb_slug"),
        Index("ix_wiki_pages_kb_type", "knowledge_base_id", "page_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    page_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "wiki_page_versions.id",
            name="fk_wiki_pages_current_version_id",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WikiPageVersion(Base):
    """正文与来源不可变；审核状态可以单向转换。"""

    __tablename__ = "wiki_page_versions"
    __table_args__ = (
        UniqueConstraint("page_id", "version_no", name="uq_wiki_page_version_no"),
        Index("ix_wiki_versions_page_review", "page_id", "review_state"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    page_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generation_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    review_state: Mapped[str] = mapped_column(String(24), nullable=False)
    base_published_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class WikiClaim(Base):
    """页面结论的可信状态与正文锚点。"""

    __tablename__ = "wiki_claims"
    __table_args__ = (
        UniqueConstraint("page_version_id", "claim_key", name="uq_wiki_claim_version_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    page_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wiki_page_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_key: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    trust_state: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class WikiClaimSource(Base):
    """保留来源定位快照，原文失效后仍能解释旧结论。"""

    __tablename__ = "wiki_claim_sources"
    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "document_version_id",
            "chunk_kind",
            "chunk_ordinal",
            name="uq_wiki_claim_source_locator",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wiki_claims.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    chunk_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    chunk_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    chunk_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_title: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WikiJob(TimestampMixin, Base):
    """可重试且可恢复的 Wiki 生成任务。"""

    __tablename__ = "wiki_jobs"
    __table_args__ = (Index("ix_wiki_jobs_status_heartbeat", "status", "heartbeat_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    job_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class WikiTokenUsage(Base):
    """按调用幂等键累计生成占用，避免重试重复计费。"""

    __tablename__ = "wiki_token_usage"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wiki_jobs.id", ondelete="CASCADE"), nullable=False
    )
    call_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    measured_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    charged_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
