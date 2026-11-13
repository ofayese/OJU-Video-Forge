"""Local account disablement, bootstrap role, and authentication audit."""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("administrator", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("security_events")
    op.drop_column("users", "administrator")
    op.drop_column("users", "disabled")
