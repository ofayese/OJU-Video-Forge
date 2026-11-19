"""Persist job submitter principal identity."""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column("owner_principal_kind", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("owner_principal_id", sa.String(length=128), nullable=True),
    )


def downgrade():
    op.drop_column("jobs", "owner_principal_id")
    op.drop_column("jobs", "owner_principal_kind")
