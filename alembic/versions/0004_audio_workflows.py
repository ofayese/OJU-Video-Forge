"""Project-scoped audio workflow assets and artifacts.
Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

metadata = sa.MetaData()
jobs_before_audio = sa.Table(
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
    sa.UniqueConstraint("owner", "idempotency_key", name="uq_jobs_owner_idempotency"),
    sa.CheckConstraint(
        "status IN ('dispatch_pending', 'queued', 'running', 'cancel_requested', "
        "'awaiting_review', 'submitted_to_comfyui', 'failed', 'cancelled')",
        name="ck_jobs_status",
    ),
)
sa.Index("ix_jobs_owner", jobs_before_audio.c.owner)
sa.Index("ix_jobs_project_id", jobs_before_audio.c.project_id)
sa.Index("ix_jobs_media_id", jobs_before_audio.c.media_id)
sa.Index("ix_jobs_retry_of_job_id", jobs_before_audio.c.retry_of_job_id)


def upgrade():
    with op.batch_alter_table('jobs', copy_from=jobs_before_audio) as batch_op:
        batch_op.create_unique_constraint('uq_jobs_retry_of_id', ['retry_of_job_id'])
    op.create_table(
        'audio_assets',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('owner', sa.String(128), nullable=False),
        sa.Column('relative_path', sa.String(512), nullable=False, unique=True),
        sa.Column('media_type', sa.String(64), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_audio_assets_project_id', 'audio_assets', ['project_id'])
    op.create_index('ix_audio_assets_owner', 'audio_assets', ['owner'])
    op.create_table(
        'workflow_artifacts',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('job_id', sa.String(40), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('owner', sa.String(128), nullable=False),
        sa.Column('relative_path', sa.String(512), nullable=False, unique=True),
        sa.Column('filename', sa.String(128), nullable=False),
        sa.Column('media_type', sa.String(64), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_workflow_artifacts_job_id', 'workflow_artifacts', ['job_id'])
    op.create_index('ix_workflow_artifacts_project_id', 'workflow_artifacts', ['project_id'])
    op.create_index('ix_workflow_artifacts_owner', 'workflow_artifacts', ['owner'])


def downgrade():
    op.drop_index('ix_workflow_artifacts_owner', table_name='workflow_artifacts')
    op.drop_index('ix_workflow_artifacts_project_id', table_name='workflow_artifacts')
    op.drop_index('ix_workflow_artifacts_job_id', table_name='workflow_artifacts')
    op.drop_table('workflow_artifacts')
    op.drop_index('ix_audio_assets_owner', table_name='audio_assets')
    op.drop_index('ix_audio_assets_project_id', table_name='audio_assets')
    op.drop_table('audio_assets')
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_constraint('uq_jobs_retry_of_id', type_='unique')
