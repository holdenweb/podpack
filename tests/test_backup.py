"""What a site's backup has to include, and who said so.

The point of the declaration is that a *simple* app needs none of it, so most
of these tests are about what podpack works out on its own. The rest are about
the one thing it cannot see -- an empty directory, which means "stateless" or
"the mount never arrived" and looks identical either way.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from podpack import Backup, SiteApp
from podpack.cli import main
from podpack.registry import import_app_models, installed_site_apps
from podpack.services import CATALOGUE

AppPackage = Callable[[str, str], str]


def _app(name: str, declaration: str = "") -> str:
    """Source for a minimal app, with whatever declaration the test needs."""
    return f'''
from flask import Blueprint
from podpack import Backup, SiteApp

blueprint = Blueprint("{name}", __name__)
site_app = SiteApp(blueprint=blueprint{declaration})
'''


def _storing_app(name: str, tag: str, declaration: str = "") -> str:
    """An app with a table of its own, so attribution has something to find."""
    return f'''
from flask import Blueprint
from podpack import Backup, SiteApp, db


class Thing(db.Model):
    __tablename__ = "{name}_things_{tag}"
    id = db.Column(db.Integer, primary_key=True)


blueprint = Blueprint("{name}", __name__)
site_app = SiteApp(blueprint=blueprint{declaration})
'''


def _config(tmp_path: Path, *apps: str) -> Path:
    path = tmp_path / "plan_app.toml"
    listed = ", ".join(f'"{name}"' for name in apps)
    path.write_text(f'[site]\nname = "planned"\napps = [{listed}]\n')
    return path


def _plan(capsys: pytest.CaptureFixture[str], config: Path) -> dict:
    assert main(["backup", "plan", "--config", str(config)]) == 0
    return json.loads(capsys.readouterr().out)


def _named(plan: dict, name: str) -> dict:
    (entry,) = [app for app in plan["apps"] if app["name"] == name]
    return entry


# ---------------------------------------------------------------------------
# What an app gets without declaring anything.
# ---------------------------------------------------------------------------


def test_an_app_that_declares_nothing_is_backed_up_in_full(
    app_package: AppPackage, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole thesis. Silence must mean "keep everything", not "keep none"."""
    app_package("quiet_app", _app("quiet_app"))
    entry = _named(_plan(capsys, _config(tmp_path, "quiet_app")), "quiet_app")

    assert entry["data"] is True
    assert entry["declared"] is False


def test_an_apps_tables_are_attributed_without_being_declared(
    app_package: AppPackage, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Read from the mapper registry, which is a fact rather than a claim."""
    app_package("rows_app", _storing_app("rows_app", "attr"))
    entry = _named(_plan(capsys, _config(tmp_path, "rows_app")), "rows_app")

    assert entry["tables"] == ["rows_app_things_attr"]
    assert entry["declared"] is False


# ---------------------------------------------------------------------------
# The exceptions, which are the only reason the declaration exists.
# ---------------------------------------------------------------------------


def test_a_stateless_app_says_so_and_is_distinguishable_from_silence(
    app_package: AppPackage, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`Backup(data=False)` and `None` both describe an empty directory.

    Only one of them is somebody's answer, and a backup that cannot tell them
    apart cannot report that nobody has thought about an app.
    """
    app_package("stateless_app", _app("stateless_app", ", backs_up=Backup(data=False)"))
    app_package("silent_app", _app("silent_app"))
    plan = _plan(capsys, _config(tmp_path, "stateless_app", "silent_app"))

    assert _named(plan, "stateless_app") == {
        "name": "stateless_app", "declared": True, "data": False,
        "excludes": [], "extra": [], "reseedable": False, "tables": [],
    }
    assert _named(plan, "silent_app")["data"] is True
    assert _named(plan, "silent_app")["declared"] is False


def test_derived_subtrees_and_outside_state_are_carried_through(
    app_package: AppPackage, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app_package(
        "fussy_app",
        _app(
            "fussy_app",
            ", backs_up=Backup(excludes=frozenset({'thumbnails', 'cache'}),"
            " extra=('shared/uploads',), reseedable=True)",
        ),
    )
    entry = _named(_plan(capsys, _config(tmp_path, "fussy_app")), "fussy_app")

    # Sorted, so a frozenset's iteration order cannot make the plan differ
    # between runs -- a backup script diffing two plans would see phantom
    # changes.
    assert entry["excludes"] == ["cache", "thumbnails"]
    assert entry["extra"] == ["shared/uploads"]
    assert entry["reseedable"] is True


def test_the_plan_carries_no_absolute_paths(
    app_package: AppPackage, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """It is computed in the container and consumed on the host.

    The data root differs between the two (`/var/lib/<site>/apps` against
    `$HOST_DATA_DIR/apps`), so a path that crossed would be wrong at exactly
    one end. Names only; the script joins them to its own root.
    """
    app_package("pathless_app", _app("pathless_app", ", backs_up=Backup(extra=('sub/dir',))"))
    plan = _plan(capsys, _config(tmp_path, "pathless_app"))

    # Every string the script will join to a root, checked individually --
    # scanning the rendered JSON would also pass if the fields were empty.
    entry = _named(plan, "pathless_app")
    subpaths = [entry["name"], *entry["excludes"], *entry["extra"]]
    assert subpaths == ["pathless_app", "sub/dir"]
    assert not [path for path in subpaths if path.startswith("/")]
    assert "/var/lib" not in json.dumps(plan)


# ---------------------------------------------------------------------------
# Services: what compose merged, not what a file claimed.
# ---------------------------------------------------------------------------


def test_services_come_from_the_markers_compose_stamped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PODPACK_SERVICE_MARKERS", "1")
    monkeypatch.setenv("PODPACK_SERVICE_POSTGRES", "1")
    monkeypatch.delenv("PODPACK_SERVICE_MONGODB", raising=False)
    plan = _plan(capsys, _config(tmp_path))

    assert [service["name"] for service in plan["services"]] == ["postgres"]
    assert plan["in_container"] is True


def test_no_markers_means_no_services_claimed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run outside a container, the plan says it does not know.

    Guessing would be worse than silence: a backup script that took an empty
    service list for "this site has no database" would write a directory that
    looks like a backup and restores nothing.
    """
    for name, service in CATALOGUE.items():
        monkeypatch.delenv(service.marker_env, raising=False)
    monkeypatch.delenv("PODPACK_SERVICE_MARKERS", raising=False)
    plan = _plan(capsys, _config(tmp_path))

    assert plan["services"] == []
    assert plan["in_container"] is False


def test_every_catalogued_service_knows_how_to_dump_itself() -> None:
    """The regression that prompted all of this.

    A site enabling MongoDB got no backup of it at all, because the only
    backup script in existence hardcoded `pg_dump`. A service added to the
    catalogue without a dump command would put the next site in the same
    position, silently.
    """
    for name, service in CATALOGUE.items():
        assert service.dump, f"{name} has no dump command"
        assert service.restore, f"{name} has no restore command"
        assert service.dump_file, f"{name} has no dump file name"


def test_a_service_with_no_dump_is_reported_rather_than_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silence about a store is the one outcome worse than an error."""
    from dataclasses import replace

    crippled = {"postgres": replace(CATALOGUE["postgres"], dump="")}
    monkeypatch.setattr("podpack.services.CATALOGUE", crippled)
    monkeypatch.setenv("PODPACK_SERVICE_MARKERS", "1")
    monkeypatch.setenv("PODPACK_SERVICE_POSTGRES", "1")
    plan = _plan(capsys, _config(tmp_path))

    (service,) = plan["services"]
    assert service["dump"] is None
    assert "no dump command" in service["problem"]


# ---------------------------------------------------------------------------
# The traversal this is built on.
# ---------------------------------------------------------------------------


def test_reading_declarations_needs_no_application(
    app_package: AppPackage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0010's property, which is what lets this be a CLI command.

    The same assertion `test_migration_metadata_needs_no_application` makes,
    for the same reason: if this ever needed a secret key or a reachable
    database, `podpack backup plan` would stop working in exactly the
    circumstances a backup matters.
    """
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)
    monkeypatch.delenv("SECURITY_PASSWORD_SALT", raising=False)
    app_package("appless_app", _storing_app("appless_app", "noapp"))

    installed = installed_site_apps(["appless_app"])

    assert isinstance(installed.apps["appless_app"], SiteApp)
    assert installed.defined_by["appless_app_things_noapp"] == "appless_app"


def test_import_app_models_still_registers_models(app_package: AppPackage) -> None:
    """It became a wrapper; alembic still depends on what it does.

    ADR-0010 warns that each traversal of the app list is another thing to
    keep in step, which is why there is still only one implementation -- but
    the old entry point has a caller that must not notice.
    """
    from podpack import db

    app_package("wrapped_app", _storing_app("wrapped_app", "wrap"))
    import_app_models(["wrapped_app"])

    assert "wrapped_app_things_wrap" in db.metadata.tables


def test_backup_defaults_keep_everything() -> None:
    """The default has to be the safe direction, not the tidy one."""
    assert Backup() == Backup(data=True, excludes=frozenset(), extra=(), reseedable=False)
    assert SiteApp.__dataclass_fields__["backs_up"].default is None


# ---------------------------------------------------------------------------
# The boot check, which reports rather than refuses.
# ---------------------------------------------------------------------------


@pytest.fixture
def package(tmp_path: Path):
    """Build a real package in a directory of its own, optionally shipping data.

    Not `app_package`, which writes a single module straight into `tmp_path`
    -- the same `tmp_path` the `site` fixture roots its data directory in.
    `importlib.resources.files()` on a single-module app resolves to the
    module's *parent*, so `_seed_data` then finds `tmp_path/data`, which is
    the site's whole data root, and seeds it into the app's own directory.
    That is the stray-`data/`-beside-the-module trap writing-an-app.md warns
    about, reached here by accident; a package with its own directory cannot
    hit it.
    """
    import sys

    root = tmp_path / "packages"
    root.mkdir()
    sys.path.insert(0, str(root))
    created: list[str] = []

    def _make(name: str, source: str, ships: dict[str, str] | None = None) -> str:
        (root / name).mkdir()
        (root / name / "__init__.py").write_text(source)
        if ships:
            (root / name / "data").mkdir()
            for filename, content in ships.items():
                (root / name / "data" / filename).write_text(content)
        created.append(name)
        return name

    yield _make

    sys.path.remove(str(root))
    for name in created:
        sys.modules.pop(name, None)


def _site_with(site, name: str):
    return site(host_config={"site": {"name": "x", "environment": "test", "apps": [name]}})


def test_a_stateless_claim_contradicted_by_the_disk_is_warned_about(
    site, package, caplog: pytest.LogCaptureFixture
) -> None:
    """An app shipping data while claiming to store nothing.

    A backup skips it on the app's word, so nobody would find out until a
    restore produced an app missing files it had always had.
    """
    package(
        "shipper_app",
        _app("shipper_app", ", backs_up=Backup(data=False)"),
        ships={"content.md": "kept\n"},
    )
    with caplog.at_level("WARNING"):
        _site_with(site, "shipper_app")

    assert "says it stores nothing" in caplog.text
    assert "content.md" in caplog.text


def test_an_empty_directory_upholds_the_claim_and_says_nothing(
    site, package, caplog: pytest.LogCaptureFixture
) -> None:
    """The ordinary case for a stateless app, which must be silent.

    A warning every boot for the correct state is how people learn to stop
    reading the boot log.
    """
    package("truthful_app", _app("truthful_app", ", backs_up=Backup(data=False)"))
    with caplog.at_level("WARNING"):
        _site_with(site, "truthful_app")

    assert "says it stores nothing" not in caplog.text


def test_an_undeclared_app_is_never_warned_about(
    site, package, caplog: pytest.LogCaptureFixture
) -> None:
    """Silence is a legitimate state: backed up in full, and not nagged about."""
    package("unopinionated_app", _app("unopinionated_app"), ships={"a.md": "x"})
    with caplog.at_level("WARNING"):
        _site_with(site, "unopinionated_app")

    assert "says it stores nothing" not in caplog.text


# ---------------------------------------------------------------------------
# What an operator sees.
# ---------------------------------------------------------------------------


def test_status_reports_the_declaration_and_distinguishes_silence(site, package) -> None:
    package("shown_app", _app("shown_app", ", backs_up=Backup(data=False)"))
    package("mute_app", _app("mute_app"))
    built = site(
        host_config={
            "site": {"name": "x", "environment": "test", "apps": ["shown_app", "mute_app"]}
        }
    )
    apps = built.test_client().get("/_status").get_json()["apps"]

    assert apps["shown_app"]["backs_up"] == {
        "data": False, "excludes": [], "extra": [], "reseedable": False,
    }
    # Present and null, not absent: "nobody said" is an answer worth showing
    # an operator, and a missing key would read as an older podpack.
    assert apps["mute_app"]["backs_up"] is None
