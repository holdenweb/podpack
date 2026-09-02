"""A migration run through the shipped env.py must actually commit on PostgreSQL.

This is the test the rest of the suite structurally cannot be. Everything else
runs on SQLite, where `refuse_a_missing_schema` returns early (it asks nothing
of a non-PostgreSQL engine) -- so the failure this guards never appears there:

    refuse_a_missing_schema()'s `SELECT current_schema()` auto-begins a
    transaction under SQLAlchemy 2.0; alembic then declines to commit a
    transaction it did not open; run_migrations_online never commits either; and
    the NullPool engine closing the connection rolls the whole `upgrade` back.
    `alembic upgrade head` reports success and exits 0 while leaving no tables
    and no alembic_version -- a fresh site boots with an empty schema and every
    login 500s on `relation "user" does not exist`.

It ran the *shipped* env.py (the code under test), via `alembic upgrade head`,
against a real PostgreSQL, and then asks a *fresh* connection whether the tables
are there -- which is exactly the question the bug answered "no".

Opt-in: set PODPACK_TEST_DATABASE_URI to a throwaway PostgreSQL (CI wires one
from a service); skipped otherwise, so the SQLite suite runs anywhere.
"""

import os
import uuid
from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine, pool, text

PG = os.environ.get("PODPACK_TEST_DATABASE_URI")

pytestmark = pytest.mark.skipif(
    not PG,
    reason="set PODPACK_TEST_DATABASE_URI to a throwaway PostgreSQL to run the "
    "migration-persistence test",
)

# The env.py under test is the shipped canonical, the one a site actually gets.
SHIPPED_ALEMBIC = (
    Path(__file__).parents[1] / "src" / "podpack" / "substrate" / "data" / "alembic"
)

# A minimal hand-written migration: one table, no metadata needed. The bug is
# not about *what* is migrated, only about whether the migration commits.
TEST_MIGRATION = '''\
"""probe"""
from alembic import op
import sqlalchemy as sa

revision = "probe0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("probe", sa.Column("id", sa.Integer(), primary_key=True))


def downgrade():
    op.drop_table("probe")
'''


def _url_in(schema: str) -> str:
    """The PG url, routed into one throwaway schema by search_path.

    Per-connection (via libpq `options`), not a role default, so nothing this
    test does outlives it -- and it puts `current_schema()` on a schema that
    exists, exactly the healthy state `refuse_a_missing_schema` expects in
    production, so the code path under test is the production one.
    """
    sep = "&" if "?" in PG else "?"
    return f"{PG}{sep}options=-csearch_path%3D{schema}"


def test_upgrade_head_persists_its_tables(tmp_path, monkeypatch):
    schema = f"mtest_{uuid.uuid4().hex[:8]}"
    admin = create_engine(PG, poolclass=pool.NullPool, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        url = _url_in(schema)

        # Sanity-check the routing before trusting a negative result: if the
        # search_path did not take, a later "table missing" would blame env.py
        # for a setup fault. It must be the schema we made.
        probe = create_engine(url, poolclass=pool.NullPool)
        with probe.connect() as c:
            assert c.execute(text("show search_path")).scalar() == schema
        probe.dispose()

        monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", url)
        cfg_toml = tmp_path / "app.toml"
        cfg_toml.write_text('[site]\nname = "migrate-test"\napps = []\n')
        monkeypatch.setenv("PODPACK_CONFIG", str(cfg_toml))

        versions = tmp_path / "versions"
        versions.mkdir()
        (versions / "probe0001.py").write_text(TEST_MIGRATION)

        cfg = Config()
        cfg.set_main_option("script_location", str(SHIPPED_ALEMBIC))
        cfg.set_main_option("path_separator", "os")  # one path; silence the split warning
        cfg.set_main_option("version_locations", str(versions))
        upgrade(cfg, "head")  # runs the shipped env.py's run_migrations_online

        # The whole point: a *fresh* connection. The bug committed nothing, so a
        # new session saw neither the table nor alembic's own version row.
        check = create_engine(PG, poolclass=pool.NullPool)
        with check.connect() as c:
            table = c.execute(text(f"select to_regclass('{schema}.probe')")).scalar()
            version = c.execute(
                text(f"select to_regclass('{schema}.alembic_version')")
            ).scalar()
        check.dispose()

        assert table is not None, (
            "alembic reported a clean upgrade but the table is gone -- "
            "run_migrations_online is not committing (see env.py)"
        )
        assert version is not None, "alembic_version did not persist either"
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
