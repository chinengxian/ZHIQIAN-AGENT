"""增加 Wiki 来源驱动混合检索所需结构。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261006_0003"
down_revision: str | None = "20260922_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 元数据模糊召回依赖 trigram；权限不足时迁移必须显式失败。
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "wiki_page_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["wiki_pages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "page_id", "normalized_alias", name="uq_wiki_page_alias_normalized"
        ),
    )
    op.create_index(
        "ix_wiki_aliases_normalized", "wiki_page_aliases", ["normalized_alias"]
    )
    op.add_column(
        "wiki_claims",
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "wiki_claims",
        sa.Column(
            "claim_type",
            sa.String(24),
            server_default="source_excerpt",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_wiki_claim_type",
        "wiki_claims",
        "claim_type IN ('summary_fact', 'topic_fact', 'source_excerpt', 'manual')",
    )
    op.create_index("ix_wiki_claim_sources_chunk", "wiki_claim_sources", ["chunk_id"])
    op.create_index(
        "ix_wiki_claim_sources_locator",
        "wiki_claim_sources",
        ["document_version_id", "chunk_kind", "chunk_ordinal"],
    )
    op.execute(
        "CREATE INDEX ix_wiki_pages_title_trgm ON wiki_pages "
        "USING gin (lower(title) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_wiki_versions_summary_trgm ON wiki_page_versions "
        "USING gin (lower(summary) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_wiki_aliases_value_trgm ON wiki_page_aliases "
        "USING gin (lower(alias) gin_trgm_ops)"
    )


def downgrade() -> None:
    # pg_trgm 可能被其他模块共用，回滚时不删除扩展。
    op.execute("DROP INDEX IF EXISTS ix_wiki_aliases_value_trgm")
    op.execute("DROP INDEX IF EXISTS ix_wiki_versions_summary_trgm")
    op.execute("DROP INDEX IF EXISTS ix_wiki_pages_title_trgm")
    op.drop_index("ix_wiki_claim_sources_locator", table_name="wiki_claim_sources")
    op.drop_index("ix_wiki_claim_sources_chunk", table_name="wiki_claim_sources")
    op.drop_constraint("ck_wiki_claim_type", "wiki_claims", type_="check")
    op.drop_column("wiki_claims", "claim_type")
    op.drop_column("wiki_claims", "position")
    op.drop_index("ix_wiki_aliases_normalized", table_name="wiki_page_aliases")
    op.drop_table("wiki_page_aliases")
