"""Alembic environment for a podpack site.

The one thing worth understanding here: the metadata alembic compares against is
assembled by importing the models of every app the *site configuration* says is
installed. Migrations therefore follow the app list, and adding an app to
`app.toml` is what makes its tables visible to autogenerate.

See `podpack.migrations.target_metadata` for the consequence of that -- namely
that autogenerate will propose dropping the tables of an app you have disabled.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from podpack.migrations import target_metadata as _target_metadata

# A site that keeps its environment in a .env file gets it loaded here too,
# so host-side alembic runs see the same variables the site does. Guarded:
# python-dotenv is the site's dependency if it is anyone's, not podpack's.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The same environment variable the application itself uses, so alembic and the
# running site can never disagree about which database they mean.
db_url = os.environ.get("SQLALCHEMY_DATABASE_URI")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# The same default the application uses in development: the site's config file
# at its conventional in-repo path. In the container, PODPACK_CONFIG is set and
# points at the mounted copy, exactly as it is for the running site.
target_metadata = _target_metadata(os.environ.get("PODPACK_CONFIG", "config/app.toml"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
