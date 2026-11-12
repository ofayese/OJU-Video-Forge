"""Add project/media records and durable job dispatch.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

metadata = sa.MetaData()
jobs_before_dispatch = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("owner", sa.String(128), nullable=False),
    sa.Column("operation", sa.String(32), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("spec", sa.JSON(), nullable=False),
    sa.Column("artifact", sa.Text(), nullable=True),
    sa.Column("error", sa.Text(), nullable=True),
    sa.Column("idempotency_key", sa.String(128), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Index("ix_jobs_owner", jobs_before_dispatch.c.owner)


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
    with op.batch_alter_table("jobs", copy_from=jobs_before_dispatch) as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("media_id", sa.String(40), nullable=True))
        batch_op.add_column(
            sa.Column(
                "dispatch_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("dispatch_error", sa.String(64), nullable=True))
        batch_op.add_column(
            sa.Column("retry_of_job_id", sa.String(40), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_jobs_project_id_projects", "projects", ["project_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_jobs_media_id_media_assets",
            "media_assets",
            ["media_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_jobs_retry_of_job_id_jobs", "jobs", ["retry_of_job_id"], ["id"]
        )
        batch_op.create_index("ix_jobs_project_id", ["project_id"])
        batch_op.create_index("ix_jobs_media_id", ["media_id"])
        batch_op.create_index("ix_jobs_retry_of_job_id", ["retry_of_job_id"])
        batch_op.create_unique_constraint(
            "uq_jobs_owner_idempotency", ["owner", "idempotency_key"]
        )
        batch_op.create_check_constraint(
            "ck_jobs_status",
            "status IN ('dispatch_pending', 'queued', 'running', 'cancel_requested', "
            "'awaiting_review', 'approved', 'rejected', 'submitted_to_comfyui', "
            "'failed', 'cancelled')",
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
        sa.CheckConstraint(
            "status IN ('pending', 'dispatching', 'dispatched', 'cancelled')",
            name="ck_dispatch_outbox_status",
        ),
    )
    op.create_index("ix_dispatch_outbox_available_at", "dispatch_outbox", ["available_at"])
    op.execute(
        sa.text(
            "INSERT INTO dispatch_outbox "
            "(job_id, status, attempts, available_at, created_at) "
            "SELECT id, 'pending', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM jobs WHERE status IN ('dispatch_pending', 'queued')"
        )
    )


def downgrade():
    op.drop_table("dispatch_outbox")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_constraint("ck_jobs_status", type_="check")
        batch_op.drop_constraint("uq_jobs_owner_idempotency", type_="unique")
        batch_op.drop_index("ix_jobs_media_id")
        batch_op.drop_index("ix_jobs_project_id")
        batch_op.drop_index("ix_jobs_retry_of_job_id")
        batch_op.drop_constraint("fk_jobs_media_id_media_assets", type_="foreignkey")
        batch_op.drop_constraint("fk_jobs_project_id_projects", type_="foreignkey")
        batch_op.drop_constraint("fk_jobs_retry_of_job_id_jobs", type_="foreignkey")
        batch_op.drop_column("retry_of_job_id")
        batch_op.drop_column("dispatch_error")
        batch_op.drop_column("dispatch_attempts")
        batch_op.drop_column("media_id")
        batch_op.drop_column("project_id")
    op.drop_index("ix_media_assets_project_id", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index("ix_projects_owner", table_name="projects")
    op.drop_table("projects")
