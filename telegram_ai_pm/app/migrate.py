from pathlib import Path
from alembic import command
from alembic.config import Config


def upgrade():
    config = Config(str(Path(__file__).resolve().parent.parent / 'alembic.ini'))
    command.upgrade(config, 'head')


if __name__ == '__main__':
    upgrade()
