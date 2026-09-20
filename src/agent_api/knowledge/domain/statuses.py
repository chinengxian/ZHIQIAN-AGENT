from enum import StrEnum


class DocumentStatus(StrEnum):
    """用户视角的文档总体状态；活动版本切换成功后才会进入 ready。"""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class VersionStatus(StrEnum):
    """单个文档版本在异步入库管线中的细粒度阶段。"""

    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class ChunkKind(StrEnum):
    """父块提供上下文，子块进入 Milvus 参与召回。"""

    PARENT = "parent"
    CHILD = "child"


class IngestionJobStatus(StrEnum):
    """可恢复任务状态；retry_wait 表示等待下一次指数退避重试。"""

    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    """事务内事件投递状态，用于避免数据库已提交但队列消息丢失。"""

    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
