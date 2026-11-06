"""Add project/media records and durable job dispatch.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_owner", "projects", ["owner"])
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_assets_project_id", "media_assets", ["project_id"])
    op.add_column("jobs", sa.Column("project_id", sa.String(40), nullable=True))
    op.add_column("jobs", sa.Column("media_id", sa.String(40), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("jobs", sa.Column("dispatch_error", sa.String(64), nullable=True))
    op.add_column("jobs", sa.Column("retry_of_job_id", sa.String(40), nullable=True))
    op.create_foreign_key("fk_jobs_project_id_projects", "jobs", "projects", ["project_id"], ["id"])
    op.create_foreign_key("fk_jobs_media_id_media_assets", "jobs", "media_assets", ["media_id"], ["id"])
    op.create_foreign_key("fk_jobs_retry_of_job_id_jobs", "jobs", "jobs", ["retry_of_job_id"], ["id"])
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_media_id", "jobs", ["media_id"])
    op.create_index("ix_jobs_retry_of_job_id", "jobs", ["retry_of_job_id"])
    op.create_unique_constraint("uq_jobs_owner_idempotency", "jobs", ["owner", "idempotency_key"])
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status IN ('dispatch_pending', 'queued', 'running', 'cancel_requested', "
        "'awaiting_review', 'submitted_to_comfyui', 'failed', 'cancelled')",
    )
    op.create_table(
        "dispatch_outbox",
        sa.Column("job_id", sa.String(40), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dispatch_outbox_available_at", "dispatch_outbox", ["available_at"])
    op.create_check_constraint(
        "ck_dispatch_outbox_status",
        "dispatch_outbox",
        "status IN ('pending', 'dispatching', 'dispatched', 'cancelled')",
    )


def downgrade():
    op.drop_constraint("ck_dispatch_outbox_status", "dispatch_outbox", type_="check")
    op.drop_index("ix_dispatch_outbox_available_at", table_name="dispatch_outbox")
    op.drop_table("dispatch_outbox")
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.drop_constraint("uq_jobs_owner_idempotency", "jobs", type_="unique")
    op.drop_index("ix_jobs_media_id", table_name="jobs")
    op.drop_index("ix_jobs_project_id", table_name="jobs")
    op.drop_index("ix_jobs_retry_of_job_id", table_name="jobs")
    op.drop_constraint("fk_jobs_media_id_media_assets", "jobs", type_="foreignkey")
    op.drop_constraint("fk_jobs_project_id_projects", "jobs", type_="foreignkey")
    op.drop_constraint("fk_jobs_retry_of_job_id_jobs", "jobs", type_="foreignkey")
    op.drop_column("jobs", "retry_of_job_id")
    op.drop_column("jobs", "dispatch_error")
    op.drop_column("jobs", "dispatch_attempts")
    op.drop_column("jobs", "media_id")
    op.drop_column("jobs", "project_id")
    op.drop_index("ix_media_assets_project_id", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index("ix_projects_owner", table_name="projects")
    op.drop_table("projects")
