import sys

import pytest

from podpack import create_app, db

# Secrets come from the environment in production; tests set them here rather
# than relying on whatever happens to be exported in the shell.
TEST_ENV = {
    "SECRET_KEY": "test-secret-key",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
}


@pytest.fixture
def host_config():
    """The kind of dict that would otherwise be read from a mounted TOML file."""
    return {
        "site": {
            "name": "test site",
            "environment": "test",
            "apps": ["podpack_notes"],
        },
        "database": {"echo": False},
        "apps": {"notes": {"page_size": 5}},
    }


@pytest.fixture
def site(monkeypatch, tmp_path, host_config):
    """Build a site whose data and log roots are throwaway directories.

    SQLite in memory and roots under tmp_path mean the registry can be tested
    without a database server or a bind mount -- but note that the seeding and
    per-app directory behaviour under test here is the same code that runs in
    the container.
    """
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)

    def _build(**overrides):
        config = {**host_config, **overrides.pop("host_config", {})}
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
def site_package(tmp_path):
    """Create an importable stand-in for a site package, with given templates.

    A real site is a package with its own `templates/`; these tests need one to
    prove template precedence, and building a throwaway is cheaper and clearer
    than shipping a fixture site in the repository.
    """
    created = []

    def _make(name, templates):
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
def app(site):
    return site()


@pytest.fixture
def client(app):
    return app.test_client()
