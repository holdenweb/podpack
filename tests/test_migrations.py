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

import pytest

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
