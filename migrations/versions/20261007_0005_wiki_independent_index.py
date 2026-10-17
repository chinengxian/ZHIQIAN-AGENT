"""增加 Wiki 页面版本独立索引生命周期与任务上下文。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261007_0005"
down_revision: str | None = "20261007_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增索引状态与任务字段，不改变历史版本的业务状态。"""

    op.add_column(
        "wiki_page_versions",
        sa.Column(
            "index_state",
            sa.String(16),
            server_default="not_required",
            nullable=False,
        ),
    )
    op.add_column(
        "wiki_page_versions",
        sa.Column("embedding_fingerprint", sa.Text(), nullable=True),
    )
    op.add_column(
        "wiki_page_versions",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "wiki_page_versions",
        sa.Column("index_error_code", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_wiki_page_version_index_state",
        "wiki_page_versions",
        "index_state IN ('not_required', 'pending', 'indexing', 'ready', 'failed')",
    )

    op.add_column(
        "wiki_jobs",
        sa.Column("page_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_wiki_jobs_page_version_id",
        "wiki_jobs",
        "wiki_page_versions",
        ["page_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "wiki_jobs",
        sa.Column("embedding_fingerprint", sa.Text(), nullable=True),
    )
    op.add_column(
        "wiki_jobs",
        sa.Column("error_reference", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_wiki_jobs_page_version_status",
        "wiki_jobs",
        ["page_version_id", "status"],
    )


def downgrade() -> None:
    """按创建逆序移除独立索引元数据，保留页面、结论与来源数据。"""

    op.drop_index("ix_wiki_jobs_page_version_status", table_name="wiki_jobs")
    op.drop_column("wiki_jobs", "error_reference")
    op.drop_column("wiki_jobs", "embedding_fingerprint")
    op.drop_constraint(
        "fk_wiki_jobs_page_version_id",
        "wiki_jobs",
        type_="foreignkey",
    )
    op.drop_column("wiki_jobs", "page_version_id")

    op.drop_constraint(
        "ck_wiki_page_version_index_state",
        "wiki_page_versions",
        type_="check",
    )
    op.drop_column("wiki_page_versions", "index_error_code")
    op.drop_column("wiki_page_versions", "indexed_at")
    op.drop_column("wiki_page_versions", "embedding_fingerprint")
    op.drop_column("wiki_page_versions", "index_state")
