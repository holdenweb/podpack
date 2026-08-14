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

from podpack.migrations import (
    refuse_foreign_autogenerate,
    target_metadata as _target_metadata,
)

# Deliberately no load_dotenv(). It was here, and it was a trap: `.env` is
# *compose's* file -- ports, host paths, COMPOSE_FILE -- and a site that
# predates that split may still have a production SQLALCHEMY_DATABASE_URI in
# it. Loading it turned "no database configured", which stops safely, into a
# silent connection to whatever `.env` happened to name. Measured on
# holdenweb.com, where a bare `alembic upgrade head` reached the live
# database with nothing exported at all.
#
# The environment comes from where it is meant to: `scripts/dev.sh` sources
# dev.env for a local run, and compose supplies env_file in a container.

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
        process_revision_directives=refuse_foreign_autogenerate,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Authoring on the engine you deploy on is the whole point of
            # authoring on the host (ADR-0011); this is what makes it true.
            process_revision_directives=refuse_foreign_autogenerate,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
