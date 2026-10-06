"""支持 Wiki 可读摘要分段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0004"
down_revision: str | None = "20261006_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展 Claim 类型，保留既有事实和来源记录。"""

    op.drop_constraint("ck_wiki_claim_type", "wiki_claims", type_="check")
    op.create_check_constraint(
        "ck_wiki_claim_type",
        "wiki_claims",
        "claim_type IN ('summary_section', 'summary_fact', 'topic_fact', 'source_excerpt', 'manual')",
    )


def downgrade() -> None:
    """有可读摘要数据时拒绝丢失语义的回滚。"""

    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM wiki_claims WHERE claim_type = 'summary_section'")
    ).scalar_one()
    if count:
        raise RuntimeError("cannot_downgrade_wiki_readable_summary_with_sections")
    op.drop_constraint("ck_wiki_claim_type", "wiki_claims", type_="check")
    op.create_check_constraint(
        "ck_wiki_claim_type",
        "wiki_claims",
        "claim_type IN ('summary_fact', 'topic_fact', 'source_excerpt', 'manual')",
    )
