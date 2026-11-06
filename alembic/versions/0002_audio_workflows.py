"""Project-scoped audio workflow assets and artifacts.
Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'projects',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('owner', sa.String(128), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_projects_owner', 'projects', ['owner'])
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column(
            'project_id', sa.String(40),
            sa.ForeignKey('projects.id', name='fk_jobs_project_id_projects'),
            nullable=True))
        batch_op.add_column(sa.Column(
            'retry_of_id', sa.String(40),
            sa.ForeignKey('jobs.id', name='fk_jobs_retry_of_id_jobs'),
            nullable=True))
        batch_op.create_unique_constraint('uq_jobs_retry_of_id', ['retry_of_id'])
    op.create_index('ix_jobs_project_id', 'jobs', ['project_id'])
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
    op.drop_index('ix_jobs_project_id', table_name='jobs')
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_constraint('uq_jobs_retry_of_id', type_='unique')
        batch_op.drop_constraint('fk_jobs_retry_of_id_jobs', type_='foreignkey')
        batch_op.drop_constraint('fk_jobs_project_id_projects', type_='foreignkey')
        batch_op.drop_column('retry_of_id')
        batch_op.drop_column('project_id')
    op.drop_index('ix_projects_owner', table_name='projects')
    op.drop_table('projects')
