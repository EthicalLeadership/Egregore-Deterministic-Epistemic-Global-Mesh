from logging.config import fileConfig
import os
from sqlalchemy import pool
from sqlalchemy import create_engine
from alembic import context

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from api.db.models import Base, Role, User, Node, Treaty, Job, Artifact
target_metadata = Base.metadata
DB_DSN = os.getenv("DB_DSN", config.get_main_option("sqlalchemy.url"))

def run_migrations_offline():
    context.configure(
        url=DB_DSN,
        target_metadata=target_metadata,
        literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = create_engine(DB_DSN, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
