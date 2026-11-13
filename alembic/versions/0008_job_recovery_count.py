"""Track bounded recovery attempts for interrupted jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("jobs", "recovery_count")
