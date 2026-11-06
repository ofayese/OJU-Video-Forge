"""Owner-scoped review contract v1, additive to existing jobs.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

from app.core import install_review_guards

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


metadata = sa.MetaData()
jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("owner", sa.String(128), nullable=False),
    sa.Column(
        "project_id",
        sa.String(40),
        sa.ForeignKey("projects.id", name="fk_jobs_project_id_projects"),
        nullable=True,
    ),
    sa.Column(
        "media_id",
        sa.String(40),
        sa.ForeignKey("media_assets.id", name="fk_jobs_media_id_media_assets"),
        nullable=True,
    ),
    sa.Column(
        "retry_of_job_id",
        sa.String(40),
        sa.ForeignKey("jobs.id", name="fk_jobs_retry_of_job_id_jobs"),
        nullable=True,
    ),
    sa.Column("operation", sa.String(32), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("spec", sa.JSON(), nullable=False),
    sa.Column("artifact", sa.Text(), nullable=True),
    sa.Column("error", sa.Text(), nullable=True),
    sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("dispatch_error", sa.String(64), nullable=True),
    sa.Column("idempotency_key", sa.String(128), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("artifact_id", sa.String(40), nullable=True),
    sa.Column("artifact_sha256", sa.String(64), nullable=True),
    sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"),
    sa.UniqueConstraint("owner", "idempotency_key", name="uq_jobs_owner_idempotency"),
    sa.UniqueConstraint("retry_of_job_id", name="uq_jobs_retry_of_id"),
    sa.CheckConstraint(
        "status IN ('dispatch_pending', 'queued', 'running', 'cancel_requested', "
        "'awaiting_review', 'approved', 'rejected', 'submitted_to_comfyui', "
        "'failed', 'cancelled')",
        name="ck_jobs_status",
    ),
)
sa.Index("ix_jobs_owner", jobs.c.owner)
sa.Index("ix_jobs_project_id", jobs.c.project_id)
sa.Index("ix_jobs_media_id", jobs.c.media_id)
sa.Index("ix_jobs_retry_of_job_id", jobs.c.retry_of_job_id)


def upgrade():
    op.add_column("jobs", sa.Column("artifact_id", sa.String(40), nullable=True))
    op.add_column("jobs", sa.Column("artifact_sha256", sa.String(64), nullable=True))
    op.add_column("jobs", sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("jobs", copy_from=jobs) as batch_op:
        batch_op.drop_constraint("ck_jobs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_jobs_status",
            "status IN ('dispatch_pending', 'queued', 'running', 'cancel_requested', "
            "'awaiting_review', 'approved', 'rejected', 'submitted_to_comfyui', "
            "'failed', 'cancelled')",
        )
    op.create_table(
        "job_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(40), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact_id", sa.String(40), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("job_id", "version", name="uq_job_reviews_version"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_job_reviews_decision"),
        sa.CheckConstraint("expected_version >= 0 AND version = expected_version + 1", name="ck_job_reviews_version"),
        sa.CheckConstraint("contract_version = 1", name="ck_job_reviews_contract"),
    )
    op.create_index("ix_job_reviews_job_id", "job_reviews", ["job_id"])
    install_review_guards(op.get_bind())


def downgrade():
    # Also blocks a downgrade through 0001, which would destroy all job evidence.
    raise RuntimeError("Review schema rollback is destructive; disable REVIEW_WORKFLOW_ENABLED instead")
