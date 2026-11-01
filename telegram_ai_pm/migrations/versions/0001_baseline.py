"""Baseline MVP schema; existing tables are adopted without dropping any data."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    def create(name, *columns):
        if name not in existing:
            op.create_table(name, *columns)

    def id_column():
        return sa.Column('id', sa.Integer(), primary_key=True)

    def created():
        return sa.Column('created_at', sa.DateTime(timezone=True))

    create('projects', id_column(), sa.Column('title', sa.String(250), nullable=False),
           sa.Column('goal', sa.Text()), sa.Column('success_criteria', sa.Text()),
           sa.Column('status', sa.String(30), nullable=False), sa.Column('scope', sa.String(20)), created())
    create('tasks', id_column(), sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id')),
           sa.Column('title', sa.String(300), nullable=False), sa.Column('description', sa.Text()),
           sa.Column('criteria', sa.Text()), sa.Column('status', sa.String(30)),
           sa.Column('priority', sa.String(2)), sa.Column('due_at', sa.DateTime(timezone=True)),
           sa.Column('reschedules', sa.Integer()), sa.Column('last_reminded_at', sa.DateTime(timezone=True)),
           sa.Column('last_checked_at', sa.DateTime(timezone=True)), created())
    create('evidence', id_column(), sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id'), nullable=False),
           sa.Column('content', sa.Text(), nullable=False), sa.Column('verified', sa.Boolean()), created())
    create('event_log', id_column(), sa.Column('kind', sa.String(60), nullable=False),
           sa.Column('object_id', sa.Integer()), sa.Column('detail', sa.JSON()), created())
    create('notifications', id_column(), sa.Column('key', sa.String(150), unique=True, nullable=False), created())
    create('messages', id_column(), sa.Column('role', sa.String(15), nullable=False),
           sa.Column('content', sa.Text(), nullable=False), created())
    create('settings', sa.Column('key', sa.String(80), primary_key=True), sa.Column('value', sa.Text(), nullable=False))


def downgrade():
    raise RuntimeError('Baseline downgrade is disabled to protect application data; restore a verified backup.')
