"""Add project-scoped agent proposals and approval audit records."""
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
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'project_memberships',
        sa.Column('project_id', sa.String(40), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('actor', sa.String(128), primary_key=True),
        sa.Column('role', sa.String(32), nullable=False),
    )
    op.add_column('jobs', sa.Column('project_id', sa.String(40), sa.ForeignKey('projects.id'), nullable=True))
    op.create_index('ix_jobs_project_id', 'jobs', ['project_id'])
    op.create_table(
        'agent_proposals',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('proposer', sa.String(128), nullable=False),
        sa.Column('tool_name', sa.String(64), nullable=False),
        sa.Column('arguments', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('approver', sa.String(128), nullable=True),
        sa.Column('job_id', sa.String(40), sa.ForeignKey('jobs.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_agent_proposals_project_id', 'agent_proposals', ['project_id'])
    op.create_table(
        'agent_audit_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('proposal_id', sa.String(40), sa.ForeignKey('agent_proposals.id'), nullable=False),
        sa.Column('job_id', sa.String(40), sa.ForeignKey('jobs.id'), nullable=True),
        sa.Column('actor', sa.String(128), nullable=False),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_agent_audit_events_project_id', 'agent_audit_events', ['project_id'])
    op.create_index('ix_agent_audit_events_proposal_id', 'agent_audit_events', ['proposal_id'])


def downgrade():
    op.drop_index('ix_agent_audit_events_proposal_id', table_name='agent_audit_events')
    op.drop_index('ix_agent_audit_events_project_id', table_name='agent_audit_events')
    op.drop_table('agent_audit_events')
    op.drop_index('ix_agent_proposals_project_id', table_name='agent_proposals')
    op.drop_table('agent_proposals')
    op.drop_index('ix_jobs_project_id', table_name='jobs')
    op.drop_column('jobs', 'project_id')
    op.drop_table('project_memberships')
    op.drop_table('projects')
