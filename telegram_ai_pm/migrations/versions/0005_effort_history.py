"""User-reported time and immutable estimate snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('complexity', sa.String(20)))
    op.add_column('tasks', sa.Column('task_type', sa.String(30)))
    op.create_table('task_time_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('minutes', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.String(32), unique=True, nullable=False),
        sa.Column('comment', sa.Text()), sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('minutes > 0', name='time_entry_positive'))
    op.create_index('ix_task_time_entries_task_id', 'task_time_entries', ['task_id'])
    op.create_table('task_estimate_revisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('minutes', sa.Integer(), nullable=False), sa.Column('breakdown', sa.JSON(), nullable=False),
        sa.Column('complexity', sa.String(20)), sa.Column('task_type', sa.String(30)),
        sa.Column('reason', sa.String(100), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('minutes >= 0', name='estimate_nonnegative'))
    op.create_index('ix_task_estimate_revisions_task_id', 'task_estimate_revisions', ['task_id'])


def downgrade():
    raise RuntimeError('Restore a verified backup; time history must not be discarded.')
