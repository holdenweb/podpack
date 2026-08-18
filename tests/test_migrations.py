"""Authoring a revision on the engine the site deploys on.

The hazard is not hypothetical: from the same models, SQLite autogenerates
`server_default=sa.text('(CURRENT_TIMESTAMP)')` where PostgreSQL writes
`now()`, and `ALTER TABLE ... ALTER COLUMN` is not SQLite syntax at all --
holdenweb.com carries both scars.

The hook is tested directly rather than through alembic. Reaching it end to
end needs a database at head *and* a model that has since changed, which is
three fixtures and a temporary model to assert one branch; and alembic
refuses an out-of-date database before the hook is ever called, so the
contrived path proves less than it costs.
"""

from types import SimpleNamespace

import ast

import pytest
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine

from podpack.migrations import AUTHORING_DIALECT, refuse_foreign_autogenerate


def _context(dialect: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect))


def _directives(empty: bool) -> list[SimpleNamespace]:
    return [SimpleNamespace(upgrade_ops=SimpleNamespace(is_empty=lambda: empty))]


def test_the_deployment_engine_is_allowed() -> None:
    refuse_foreign_autogenerate(_context(AUTHORING_DIALECT), None, _directives(empty=False))


def test_another_engine_with_changes_to_write_is_refused() -> None:
    with pytest.raises(RuntimeError) as caught:
        refuse_foreign_autogenerate(_context("sqlite"), None, _directives(empty=False))
    message = str(caught.value)
    assert "sqlite" in message and AUTHORING_DIALECT in message
    # The message has to say what to do, not merely what went wrong.
    assert "dev.env" in message
    # ...and that applying migrations is unaffected, or the reader will
    # reasonably conclude SQLite is unusable for anything.
    assert "upgrade head" in message


def test_another_engine_with_nothing_to_write_is_allowed() -> None:
    """An empty revision cannot carry a dialect's spelling of anything."""
    refuse_foreign_autogenerate(_context("sqlite"), None, _directives(empty=True))


def test_no_directives_at_all_is_allowed() -> None:
    refuse_foreign_autogenerate(_context("sqlite"), None, [])


def test_a_password_containing_percent_survives_the_migration_environment() -> None:
    """`%` is legal in a password and special to configparser.

    The shipped `alembic/env.py` used to hand the URL to
    `config.set_main_option`, which writes into a configparser where `%` is the
    interpolation escape. A deployment with such a password died at
    `ValueError: invalid interpolation syntax` before any connection was
    attempted, and the traceback named configparser rather than the password.

    This asserts the trap still exists where it lives -- so that the test fails
    loudly if a future env.py routes the URL back through the ini -- and that a
    URL carrying one survives being turned into an engine, which is what env.py
    now does with it.
    """
    url = "postgresql+psycopg2://app:pa%^ss@db:5432/site"

    with pytest.raises(ValueError, match="invalid interpolation syntax"):
        Config().set_main_option("sqlalchemy.url", url)

    assert create_engine(url).url.password == "pa%^ss"


def test_the_shipped_env_never_writes_the_url_into_the_ini() -> None:
    """The fix, asserted where it can be checked without a database.

    A url in the ini is a url through configparser, so this looks for the one
    call that would put it back. By AST and not by substring: the first version
    searched the text, and the comment explaining the trap contains the name of
    the trap, so it failed on its own documentation.
    """
    data = Path(__file__).parents[1] / "src" / "podpack" / "substrate" / "data"
    tree = ast.parse((data / "alembic" / "env.py").read_text())
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "set_main_option" not in called
