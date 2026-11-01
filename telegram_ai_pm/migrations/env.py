from alembic import context
from alembic.autogenerate import compare_metadata
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool
from app.db import Base
import os

config = context.config

if context.is_offline_mode():
    raise RuntimeError('Use an online connection: migrations inspect legacy schemas before adoption.')

engine = create_engine(os.environ['DATABASE_URL'], poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
        # Refuse to mark an incompatible legacy database as migrated.
        if (not getattr(config.cmd_opts, 'autogenerate', False)
                and context.get_context().get_current_revision() == ScriptDirectory.from_config(config).get_current_head()):
            differences = compare_metadata(context.get_context(), Base.metadata)
            if differences:
                raise RuntimeError(f'Legacy schema is incompatible; migration rolled back: {differences}')
