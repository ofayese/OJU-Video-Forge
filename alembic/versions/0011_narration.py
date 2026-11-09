"""Versioned source-video narration and script approval."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "narrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("media_id", sa.String(40), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("approved_revision", sa.Integer(), nullable=True),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.String(40), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
    )
    op.create_index("ix_narrations_project_id", "narrations", ["project_id"])
    op.create_table(
        "narration_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("narration_id", sa.String(36), sa.ForeignKey("narrations.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("script", sa.JSON(), nullable=False),
        sa.Column("editor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("narration_id", "revision"),
    )
    op.create_index("ix_narration_revisions_narration_id", "narration_revisions", ["narration_id"])


def downgrade():
    op.drop_table("narration_revisions")
    op.drop_table("narrations")
