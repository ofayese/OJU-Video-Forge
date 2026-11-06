"""Owner-scoped review contract v1, additive to existing jobs.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

from app.core import install_review_guards

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("artifact_id", sa.String(40), nullable=True))
    op.add_column("jobs", sa.Column("artifact_sha256", sa.String(64), nullable=True))
    op.add_column("jobs", sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"))
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
