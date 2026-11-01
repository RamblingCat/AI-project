"""Adopt existing effort fields and add explicit, owner-managed dependencies."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('tasks')}
    if 'estimate_minutes' not in columns:
        op.add_column('tasks', sa.Column('estimate_minutes', sa.Integer()))
    if 'effort_breakdown' not in columns:
        op.add_column('tasks', sa.Column('effort_breakdown', sa.Text()))
    if 'task_dependencies' not in inspector.get_table_names():
        op.create_table('task_dependencies',
                        sa.Column('before_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='CASCADE'), primary_key=True),
                        sa.Column('after_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='CASCADE'), primary_key=True),
                        sa.CheckConstraint('before_id <> after_id', name='dependency_not_self'))


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled; restore a verified backup instead.')
