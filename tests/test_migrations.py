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
from sqlalchemy import create_engine, text

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


def _shipped(name: str):
    """One function out of the shipped alembic env, without running the module.

    Importing it executes alembic's context at module scope, so the function is
    lifted by AST instead. Same source, no side effects.
    """
    data = Path(__file__).parents[1] / "src" / "podpack" / "substrate" / "data"
    tree = ast.parse((data / "alembic" / "env.py").read_text())
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {"text": text}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "env.py", "exec"), namespace)
    return namespace[name]


class _Connection:
    """Just enough connection to answer the two questions the check asks."""

    def __init__(self, dialect: str, **answers: object) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self.answers = answers
        self.asked: list[str] = []

    def execute(self, clause: object) -> SimpleNamespace:
        sql = str(clause).strip().lower()
        self.asked.append(sql)
        for fragment, answer in self.answers.items():
            if fragment in sql:
                return SimpleNamespace(scalar=lambda answer=answer: answer)
        raise AssertionError(f"unexpected query: {sql}")


def test_a_search_path_naming_nothing_stops_the_migration() -> None:
    """The state a recreated database leaves, and the message it now gets.

    `db-init` creates `SCHEMA app` and sets `search_path` on the *role*, which
    is cluster-level. The postgres image runs `db-init` only while its data
    directory is empty, so `dropdb`/`createdb` returns a database with no `app`
    schema and a role still pointing at it. PostgreSQL then says only "no schema
    has been selected to create in" -- naming neither the schema, nor db-init,
    nor that a bootstrap is missing. Two evenings went to that on a live host.

    Reproduced against a real PostgreSQL while this was written: with
    `search_path` set to a schema that does not exist, `current_schema()`
    returns NULL and `CREATE TABLE alembic_version` raises `InvalidSchemaName`.
    """
    refuse = _shipped("refuse_a_missing_schema")
    connection = _Connection(
        "postgresql", **{"current_schema": None, "search_path": "app"}
    )

    with pytest.raises(RuntimeError) as raised:
        refuse(connection)

    message = str(raised.value)
    # Every one of these is somewhere for the reader to go next. The whole
    # complaint about PostgreSQL's own message is that it offers none.
    for pointer in ("db-init", "CREATE SCHEMA", "POSTGRES_APP_USER", "restore.sh"):
        assert pointer in message, f"the message does not mention {pointer}"


def test_a_healthy_search_path_is_left_alone() -> None:
    refuse = _shipped("refuse_a_missing_schema")
    connection = _Connection("postgresql", **{"current_schema": "app"})

    refuse(connection)

    assert connection.asked == ["select current_schema()"], (
        "a healthy database should cost exactly one query"
    )


def test_an_engine_that_is_not_postgresql_is_never_asked() -> None:
    """`current_schema()` is not something every engine answers.

    ADR-0015 wants moving to a managed database to be a change to one variable,
    so this file stays engine-neutral. A stub that raises on any query proves
    the check issues none.
    """
    refuse = _shipped("refuse_a_missing_schema")
    connection = _Connection("sqlite")

    refuse(connection)

    assert connection.asked == []
