"""建立 Wiki 页面、来源、任务和累计 token 用量表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922_0002"
down_revision: str | None = "20260920_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:  # type: ignore[type-arg]
    """复用知识库表的 UTC 时间字段约定。"""
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    # 旧知识库缺少配置记录即表示 Wiki 关闭，迁移不会调用模型。
    op.create_table(
        "wiki_configs",
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("token_limit", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("tokens_reserved", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("tokens_charged", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("generation_model_fingerprint", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.CheckConstraint("token_limit >= 0", name="ck_wiki_config_token_limit"),
        sa.CheckConstraint("tokens_reserved >= 0", name="ck_wiki_config_reserved"),
        sa.CheckConstraint("tokens_charged >= 0", name="ck_wiki_config_charged"),
    )
    op.create_table(
        "wiki_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("page_type", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("knowledge_base_id", "slug", name="uq_wiki_page_kb_slug"),
        sa.CheckConstraint("page_type IN ('summary', 'topic', 'index')", name="ck_wiki_page_type"),
    )
    op.create_index("ix_wiki_pages_kb_type", "wiki_pages", ["knowledge_base_id", "page_type"])
    op.create_table(
        "wiki_page_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "generation_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("base_published_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["page_id"], ["wiki_pages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("page_id", "version_no", name="uq_wiki_page_version_no"),
        sa.CheckConstraint("version_no > 0", name="ck_wiki_version_positive"),
        sa.CheckConstraint(
            "origin IN ('pipeline', 'user', 'revert')", name="ck_wiki_version_origin"
        ),
        sa.CheckConstraint(
            "review_state IN ('published', 'pending_review', 'rejected', 'superseded')",
            name="ck_wiki_version_review_state",
        ),
    )
    op.create_index(
        "ix_wiki_versions_page_review", "wiki_page_versions", ["page_id", "review_state"]
    )
    op.create_foreign_key(
        "fk_wiki_pages_current_version_id",
        "wiki_pages",
        "wiki_page_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "wiki_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("page_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_key", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("trust_state", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["page_version_id"], ["wiki_page_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("page_version_id", "claim_key", name="uq_wiki_claim_version_key"),
        sa.CheckConstraint(
            "trust_state IN ('verified', 'needs_review')", name="ck_wiki_claim_trust"
        ),
    )
    op.create_table(
        "wiki_claim_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chunk_kind", sa.String(16), nullable=False),
        sa.Column("chunk_ordinal", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("document_title", sa.Text(), nullable=False),
        sa.Column(
            "heading_path",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["claim_id"], ["wiki_claims.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "claim_id",
            "document_version_id",
            "chunk_kind",
            "chunk_ordinal",
            name="uq_wiki_claim_source_locator",
        ),
    )
    op.create_index("ix_wiki_source_document", "wiki_claim_sources", ["document_id"])
    op.create_table(
        "wiki_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("stage", sa.String(32), server_default="pending", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reserved_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_wiki_job_progress"),
    )
    op.create_index("ix_wiki_jobs_status_heartbeat", "wiki_jobs", ["status", "heartbeat_at"])
    op.create_table(
        "wiki_token_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("call_key", sa.Text(), nullable=False, unique=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("measured_tokens", sa.BigInteger(), nullable=True),
        sa.Column("charged_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["wiki_jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("charged_tokens >= 0", name="ck_wiki_usage_charged"),
    )


def downgrade() -> None:
    # 回滚前应由部署者确认 Wiki 人工编辑数据已备份。
    op.drop_table("wiki_token_usage")
    op.drop_index("ix_wiki_jobs_status_heartbeat", table_name="wiki_jobs")
    op.drop_table("wiki_jobs")
    op.drop_index("ix_wiki_source_document", table_name="wiki_claim_sources")
    op.drop_table("wiki_claim_sources")
    op.drop_table("wiki_claims")
    op.drop_constraint("fk_wiki_pages_current_version_id", "wiki_pages", type_="foreignkey")
    op.drop_index("ix_wiki_versions_page_review", table_name="wiki_page_versions")
    op.drop_table("wiki_page_versions")
    op.drop_index("ix_wiki_pages_kb_type", table_name="wiki_pages")
    op.drop_table("wiki_pages")
    op.drop_table("wiki_configs")
