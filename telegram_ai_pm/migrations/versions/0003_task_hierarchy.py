"""Two-level hierarchy; legacy tasks remain roots."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('parent_task_id', sa.Integer(), nullable=True))
    op.create_foreign_key('tasks_parent_task_id_fkey', 'tasks', 'tasks', ['parent_task_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_tasks_parent_task_id', 'tasks', ['parent_task_id'])
    op.create_check_constraint('task_not_own_parent', 'tasks', 'parent_task_id <> id')
    op.execute('''CREATE FUNCTION lock_task_hierarchy() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN PERFORM pg_advisory_xact_lock(782364701); RETURN NEW; END $$''')
    op.execute('''CREATE TRIGGER task_hierarchy_lock BEFORE INSERT OR UPDATE ON tasks
                   FOR EACH ROW EXECUTE FUNCTION lock_task_hierarchy()''')
    op.execute('''CREATE FUNCTION check_task_hierarchy() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE t tasks%ROWTYPE; p tasks%ROWTYPE;
    BEGIN
      SELECT * INTO t FROM tasks WHERE id=NEW.id;
      IF NOT FOUND THEN RETURN NULL; END IF;
      IF t.parent_task_id IS NOT NULL THEN
        SELECT * INTO p FROM tasks WHERE id=t.parent_task_id;
        IF NOT FOUND OR p.parent_task_id IS NOT NULL OR p.project_id IS DISTINCT FROM t.project_id
           OR (p.status IN ('done','cancelled') AND t.status NOT IN ('done','cancelled')) THEN
          RAISE EXCEPTION 'Invalid parent, depth or project' USING ERRCODE='23514';
        END IF;
      END IF;
      IF EXISTS(SELECT 1 FROM tasks c WHERE c.parent_task_id=t.id AND
        (t.parent_task_id IS NOT NULL OR c.project_id IS DISTINCT FROM t.project_id OR
         (t.status IN ('done','cancelled') AND c.status NOT IN ('done','cancelled')))) THEN
        RAISE EXCEPTION 'Invalid children or unfinished work' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$''')
    op.execute('''CREATE CONSTRAINT TRIGGER task_hierarchy_check AFTER INSERT OR UPDATE ON tasks
                  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_task_hierarchy()''')


def downgrade():
    raise RuntimeError('Restore a verified backup; hierarchy downgrade would lose relationships.')
