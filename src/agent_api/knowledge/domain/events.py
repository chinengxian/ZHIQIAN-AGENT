from enum import StrEnum


class OutboxEventType(StrEnum):
    """知识模块通过事务 Outbox 投递的异步事件类型。"""

    # 请求解析、分块、向量化并激活文档版本。
    DOCUMENT_INGESTION_REQUESTED = "document.ingestion.requested"
    # 请求删除单篇文档及其文件和检索索引。
    DOCUMENT_DELETION_REQUESTED = "document.deletion.requested"
    # 请求清理已失效或处理失败的文档版本资源。
    DOCUMENT_VERSION_CLEANUP_REQUESTED = "document.version.cleanup.requested"
    # 请求删除知识库及其关联数据和检索资源。
    KNOWLEDGE_BASE_DELETION_REQUESTED = "knowledge_base.deletion.requested"
    # 请求根据活动文档版本生成或恢复 Wiki 内容。
    WIKI_DOCUMENT_GENERATE_REQUESTED = "wiki.document.generate.requested"
