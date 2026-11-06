"""Track bounded recovery attempts for interrupted jobs."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("jobs", "recovery_count")
