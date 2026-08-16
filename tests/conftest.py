import gc
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from podpack import create_app, db

# What the `site` fixture hands back: podpack's factory with the test's roots
# and host config already bound in, so a test names only what it is varying.
SiteFactory = Callable[..., Flask]

# `apps = ["fixture_app"]` has to resolve like any other installed app, so the
# tests directory goes on the path exactly as site-packages would be.
sys.path.insert(0, str(Path(__file__).parent))

# Secrets come from the environment in production; tests set them here rather
# than relying on whatever happens to be exported in the shell.
TEST_ENV = {
    "SECRET_KEY": "test-secret-key",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    # Required since login became core: flask-security keys its password hashes
    # on this and refuses to hash without it.
    "SECURITY_PASSWORD_SALT": "test-password-salt",
}


@pytest.fixture
def host_config() -> dict[str, Any]:
    """The kind of dict that would otherwise be read from a mounted TOML file."""
    return {
        "site": {
            "name": "test site",
            "environment": "test",
            "apps": ["fixture_app"],
        },
        "database": {"echo": False},
        "apps": {"widget": {"size": 5}},
    }


@pytest.fixture
def site(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host_config: dict[str, Any]
) -> SiteFactory:
    """Build a site whose data and log roots are throwaway directories.

    SQLite in memory and roots under tmp_path mean the registry can be tested
    without a database server or a bind mount -- but note that the seeding and
    per-app directory behaviour under test here is the same code that runs in
    the container.
    """
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)

    def _build(**overrides: Any) -> Flask:
        config = {**host_config, **overrides.pop("host_config", {})}
        # A test site is an operator's by default, so that the many tests
        # reading /_status need not each say so. The guard itself is tested
        # by passing something else.
        overrides.setdefault("admin", lambda: True)
        app = create_app(
            host_config=config,
            data_root=tmp_path / "data",
            log_root=tmp_path / "logs",
            **overrides,
        )
        with app.app_context():
            db.create_all()
        return app

    return _build


@pytest.fixture
def site_package(tmp_path: Path) -> Iterator[Callable[[str, dict[str, str]], str]]:
    """Create an importable stand-in for a site package, with given templates.

    A real site is a package with its own `templates/`; these tests need one to
    prove template precedence, and building a throwaway is cheaper and clearer
    than shipping a fixture site in the repository.
    """
    created = []

    def _make(name: str, templates: dict[str, str]) -> str:
        root = tmp_path / name
        (root / "templates").mkdir(parents=True)
        (root / "__init__.py").write_text("")
        for path, content in templates.items():
            target = root / "templates" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        created.append(name)
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))
        return name

    yield _make

    for name in created:
        sys.modules.pop(name, None)
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


@pytest.fixture
def app_package(tmp_path: Path) -> Iterator[Callable[[str, str], str]]:
    """Create an importable single-module podpack app with the given source.

    Cheaper and clearer than shipping a second fixture app in the repository,
    and it lets a test install something `tests/fixture_app` deliberately is
    not -- an app mounted at the site root, say.
    """
    created = []

    def _make(name: str, source: str) -> str:
        (tmp_path / f"{name}.py").write_text(source)
        created.append(name)
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))
        return name

    yield _make

    for name in created:
        sys.modules.pop(name, None)
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))
    # A test app whose import *failed* (the table-clash test induces exactly
    # that) leaves a half-constructed model class alive only through its
    # traceback; SQLAlchemy's registry holds it weakly and trips over it on
    # the next mapper sweep -- but only if the collector has not run yet, so
    # the failure lands on whichever test follows, nondeterministically.
    # Collect deterministically instead.
    gc.collect()


@pytest.fixture
def app(site: SiteFactory) -> Flask:
    return site()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
