"""Initial jobs and audit tables.
Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('jobs',
      sa.Column('id',sa.String(40),primary_key=True),
      sa.Column('owner',sa.String(128),nullable=False),
      sa.Column('operation',sa.String(32),nullable=False),
      sa.Column('status',sa.String(32),nullable=False),
      sa.Column('spec',sa.JSON(),nullable=False),
      sa.Column('artifact',sa.Text(),nullable=True),
      sa.Column('error',sa.Text(),nullable=True),
      sa.Column('idempotency_key',sa.String(128),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_jobs_owner','jobs',['owner'])
    op.create_table('audit_events',
      sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
      sa.Column('job_id',sa.String(40),sa.ForeignKey('jobs.id'),nullable=False),
      sa.Column('actor',sa.String(128),nullable=False),
      sa.Column('action',sa.String(64),nullable=False),
      sa.Column('at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_audit_events_job_id','audit_events',['job_id'])

def downgrade():
    op.drop_index('ix_audit_events_job_id',table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index('ix_jobs_owner',table_name='jobs')
    op.drop_table('jobs')
