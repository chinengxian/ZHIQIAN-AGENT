from enum import StrEnum


class OutboxEventType(StrEnum):
    """知识模块通过事务 Outbox 投递的异步事件类型。"""

    DOCUMENT_INGESTION_REQUESTED = "document.ingestion.requested"
    DOCUMENT_DELETION_REQUESTED = "document.deletion.requested"
    DOCUMENT_VERSION_CLEANUP_REQUESTED = "document.version.cleanup.requested"
    KNOWLEDGE_BASE_DELETION_REQUESTED = "knowledge_base.deletion.requested"
    WIKI_DOCUMENT_GENERATE_REQUESTED = "wiki.document.generate.requested"
