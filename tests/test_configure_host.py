"""`scripts/configure-host.py`, which replaces the hand editing a deployment
used to need. Every failure it prevents was a real one, so each has a test.

Run as a subprocess on the host's own Python: it imports nothing from podpack
and must keep working on a machine where nothing has been installed yet.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

DATA_ROOT = Path(__file__).parents[1] / "src" / "podpack" / "substrate" / "data"
SCRIPT = DATA_ROOT / "scripts" / "configure-host.py"


def parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


@pytest.fixture
def site(tmp_path: Path) -> Path:
    # Named as a site has them: the substrate stores sources un-dotted and
    # installs them dotted, and the script reads what a site actually has.
    for source, installed in (("env.example", ".env.example"),
                              ("secrets.env.example", "secrets.env.example")):
        (tmp_path / installed).write_text((DATA_ROOT / source).read_text())
    # Copied in rather than run from the substrate tree: the script finds the
    # site from its own location, exactly as up.sh and prepare-host-dirs.sh do,
    # so running it from anywhere else configures the wrong directory. Testing
    # it where it will actually live is also the more faithful test.
    (tmp_path / "scripts").mkdir()
    installed_script = tmp_path / "scripts" / "configure-host.py"
    installed_script.write_text(SCRIPT.read_text())
    installed_script.chmod(0o755)
    return tmp_path


def configure(site: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(site / "scripts" / "configure-host.py"), *args],
        cwd=site, capture_output=True, text=True,
    )


@pytest.fixture
def configured(site: Path) -> Path:
    result = configure(site, "--port", "8461", "--site-name", "example-com")
    assert result.returncode == 0, result.stderr
    return site


def test_the_uri_agrees_with_the_role_it_names(configured: Path) -> None:
    """The failure this exists to prevent: PostgreSQL comes up healthy, having
    created the role from POSTGRES_APP_*, and `migrate` cannot authenticate
    because the URI beside them carries something else.

    Built from the parts rather than written twice, so they cannot disagree.
    """
    v = parse(configured / "secrets.env")
    assert v["SQLALCHEMY_DATABASE_URI"] == (
        f"postgresql+psycopg2://{v['POSTGRES_APP_USER']}:"
        f"{v['POSTGRES_APP_PASSWORD']}@postgres:5432/{v['POSTGRES_DB']}"
    )


def test_no_lab_credential_survives(configured: Path) -> None:
    """The examples ship working lab values so the suite comes up unedited.
    Reaching a host with them still in place is the thing to make impossible.
    """
    text = (configured / "secrets.env").read_text() + (configured / ".env").read_text()
    for lab in ("lab-only", "labadmin", "CHANGEME", "@@"):
        assert lab not in text


def test_secrets_avoid_everything_that_is_syntax_somewhere(configured: Path) -> None:
    """A generated value passes through compose's env reader, the shell, a
    PostgreSQL URI and configparser. `%` alone cost an evening: it is the
    interpolation escape in configparser, so alembic died before connecting.
    """
    v = parse(configured / "secrets.env")
    for name in ("POSTGRES_PASSWORD", "POSTGRES_APP_PASSWORD",
                 "SECRET_KEY", "SECURITY_PASSWORD_SALT"):
        assert re.fullmatch(r"[A-Za-z0-9_-]+", v[name]), f"{name} is not URL-safe"


def test_every_run_generates_different_secrets(site: Path, tmp_path: Path) -> None:
    """Two sites sharing a salt would verify each other's password hashes."""
    first = configure(site, "--port", "8461")
    assert first.returncode == 0
    one = parse(site / "secrets.env")
    second = tmp_path / "second"
    (second / "scripts").mkdir(parents=True)
    for source, installed in (("env.example", ".env.example"),
                              ("secrets.env.example", "secrets.env.example")):
        (second / installed).write_text((DATA_ROOT / source).read_text())
    (second / "scripts" / "configure-host.py").write_text(SCRIPT.read_text())
    assert configure(second, "--port", "8462").returncode == 0
    two = parse(second / "secrets.env")
    assert one["SECURITY_PASSWORD_SALT"] != two["SECURITY_PASSWORD_SALT"]
    assert one["SECRET_KEY"] != two["SECRET_KEY"]


def test_it_refuses_to_overwrite_a_configured_site(configured: Path) -> None:
    """Regenerating is not reconfiguring -- it is losing the database, because
    the role was created once from these values and every password hash is
    keyed on this salt."""
    before = (configured / "secrets.env").read_text()
    result = configure(configured, "--port", "9999")

    assert result.returncode == 1
    assert "refusing to overwrite" in result.stderr
    assert (configured / "secrets.env").read_text() == before


def test_force_is_the_way_past_that(configured: Path) -> None:
    before = parse(configured / "secrets.env")["SECURITY_PASSWORD_SALT"]
    assert configure(configured, "--port", "9999", "--force").returncode == 0
    assert parse(configured / "secrets.env")["SECURITY_PASSWORD_SALT"] != before


def test_the_files_are_not_world_readable(configured: Path) -> None:
    for name in (".env", "secrets.env"):
        assert (configured / name).stat().st_mode & 0o077 == 0, name


def test_the_examples_commentary_survives(configured: Path) -> None:
    """The comments carry most of what a reader needs -- which settings must
    never change once a site is live, and why. Substituting values line by line
    keeps them; templating from scratch would not."""
    assert (configured / "secrets.env").read_text().count("#") > 20


def test_the_port_reaches_the_env(configured: Path) -> None:
    assert parse(configured / ".env")["WEB_HOST_PORT"] == "8461"


def test_it_needs_nothing_but_the_standard_library(site: Path) -> None:
    """It runs on the host's system Python, before anything is installed --
    no venv, no podpack, no third-party package.

    By AST rather than substring, because the script's own docstring says it
    imports nothing from podpack, and a text search finds that sentence. The
    identical mistake was made an hour earlier in test_migrations.py.
    """
    import ast
    import sys as _sys

    imported: set[str] = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text())):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert "podpack" not in imported
    assert imported <= _sys.stdlib_module_names, imported - _sys.stdlib_module_names
