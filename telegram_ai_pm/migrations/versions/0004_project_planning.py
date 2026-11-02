"""Project commitments, explicit schedules and optimistic revisions."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = depends_on = None


def upgrade():
    for name in ('started_at', 'auto_due_at', 'manual_due_at', 'planned_due_at'):
        op.add_column('projects', sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column('projects', sa.Column('description', sa.Text()))
    for table in ('projects', 'tasks'):
        op.add_column(table, sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))
    for name in ('scheduled_start_at', 'scheduled_end_at'):
        op.add_column('tasks', sa.Column(name, sa.DateTime(timezone=True)))
    op.execute('''CREATE FUNCTION task_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN NEW.revision=OLD.revision+1; RETURN NEW; END $$''')
    op.execute('''CREATE TRIGGER task_revision_update BEFORE UPDATE ON tasks
                  FOR EACH ROW EXECUTE FUNCTION task_revision()''')
    op.execute('''CREATE FUNCTION project_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' THEN NEW.revision=OLD.revision+1; END IF;
      NEW.planned_due_at=COALESCE(NEW.manual_due_at,NEW.auto_due_at);
      RETURN NEW;
    END $$''')
    op.execute('''CREATE TRIGGER project_revision_update BEFORE INSERT OR UPDATE ON projects
                  FOR EACH ROW EXECUTE FUNCTION project_revision()''')
    op.execute('''CREATE FUNCTION recalculate_project_dates() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE pid integer;
    BEGIN
      FOR pid IN SELECT DISTINCT p FROM unnest(ARRAY[
          CASE WHEN TG_OP <> 'INSERT' THEN OLD.project_id END,
          CASE WHEN TG_OP <> 'DELETE' THEN NEW.project_id END]) p WHERE p IS NOT NULL ORDER BY p
      LOOP
        UPDATE projects SET auto_due_at=(SELECT max(due_at) FROM tasks
          WHERE project_id=pid AND status NOT IN ('done','cancelled')) WHERE id=pid;
      END LOOP;
      RETURN NULL;
    END $$''')
    op.execute('''CREATE TRIGGER project_dates_after_task AFTER INSERT OR UPDATE OR DELETE ON tasks
                  FOR EACH ROW EXECUTE FUNCTION recalculate_project_dates()''')


def downgrade():
    raise RuntimeError('Restore a verified backup; planning history must not be discarded.')
