"""partial index for APPROVED transfers (reconcile)

Revision ID: 0004
Revises: 0003

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_transfers_approved_updated_at",
        "transfers",
        ["updated_at"],
        unique=False,
        postgresql_where=sa.text("state = 'APPROVED'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transfers_approved_updated_at",
        table_name="transfers",
        postgresql_where=sa.text("state = 'APPROVED'"),
    )
