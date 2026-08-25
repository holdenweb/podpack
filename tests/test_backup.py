"""What a site's backup has to include, and who said so.

The point of the declaration is that a *simple* app needs none of it, so most
of these tests are about what podpack works out on its own. The rest are about
the one thing it cannot see -- an empty directory, which means "stateless" or
"the mount never arrived" and looks identical either way.
"""

import json
import os
import re
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
        assert service.verify, f"{name} has no way to check a dump it took"


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


# ---------------------------------------------------------------------------
# The contract the substrate scripts consume.
#
# `podpack backup plan` prints JSON and three shell scripts read it. Nothing
# else couples them, so renaming a key here breaks a restore rather than a
# test -- which is the wrong way round for the one facility whose failures
# are only observable at the worst possible moment.
# ---------------------------------------------------------------------------

PLAN_KEY = __import__("re").compile(r'\b(?:app|service|plan)\["([a-z_]+)"\]')


def _keys_the_scripts_read() -> dict[str, set[str]]:
    """Extract the plan keys the shipped scripts actually reference."""
    from podpack import substrate

    scripts = Path(substrate.__file__).parent / "data" / "scripts"
    found: dict[str, set[str]] = {"app": set(), "service": set(), "plan": set()}
    for script in sorted(scripts.glob("*.sh")):
        text = script.read_text()
        for match in __import__("re").finditer(
            r'\b(app|service|plan)\["([a-z_]+)"\]', text
        ):
            found[match.group(1)].add(match.group(2))
    return found


def test_the_plan_carries_every_key_the_scripts_read(
    app_package: AppPackage, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PODPACK_SERVICE_MARKERS", "1")
    monkeypatch.setenv("PODPACK_SERVICE_POSTGRES", "1")
    app_package("contract_app", _app("contract_app"))
    plan = _plan(capsys, _config(tmp_path, "contract_app"))

    read = _keys_the_scripts_read()

    # Guard the extraction before trusting it. A regex that matched nothing
    # would make every assertion below vacuously true, which is the failure
    # mode this project has already been bitten by once.
    assert len(read["app"]) >= 4, f"suspiciously few app keys found: {read['app']}"
    assert len(read["service"]) >= 4, f"suspiciously few service keys: {read['service']}"
    assert read["plan"] >= {"apps", "services"}

    for key in sorted(read["plan"]):
        assert key in plan, f"the scripts read plan[{key!r}] and the plan has no such key"
    for key in sorted(read["app"]):
        assert key in plan["apps"][0], f"the scripts read app[{key!r}], which is not emitted"
    for key in sorted(read["service"]):
        assert key in plan["services"][0], f"the scripts read service[{key!r}], not emitted"


def test_a_service_entry_carries_its_three_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """dump, restore and verify all reach the scripts that run them.

    `verify` was added to `CoreService` after the first of these tests were
    written and went several commits without anything asserting it survived
    the journey into the plan -- which is precisely how verify-backup.sh
    would have started failing on a key error nobody had exercised.
    """
    monkeypatch.setenv("PODPACK_SERVICE_MARKERS", "1")
    monkeypatch.setenv("PODPACK_SERVICE_POSTGRES", "1")
    (service,) = _plan(capsys, _config(tmp_path))["services"]

    assert service["dump"].startswith("pg_dump")
    assert service["restore"].startswith("pg_restore")
    assert service["verify"] == "pg_restore --list"
    assert service["file"] == "database.pgc"


def _restore_script() -> str:
    from podpack import substrate

    return (Path(substrate.__file__).parent / "data" / "scripts" / "restore.sh").read_text()


def test_restore_touches_the_data_directory_only_inside_the_namespace() -> None:
    """Every write into the app data root must go through `inside_namespace`.

    On Linux `prepare-host-dirs.sh` gives those directories to the containers'
    unprivileged uids with `podman unshare chown`, so from outside the namespace
    they belong to a subuid the script cannot write to. A bare `tar -xzf` there
    fails on every entry -- which is what happened on a real host, after the
    script had already replaced .env and secrets.env.

    Static, because the alternative is a Linux host with rootless podman in the
    test suite. Its value is entirely in failing when somebody adds a direct
    `tar` or `rm` back, which is exactly how the bug arrived.
    """
    body = _restore_script()

    # Guard the search before trusting it: a pattern matching nothing would make
    # the assertion below vacuously true, which this project has been bitten by.
    touches = re.findall(r'^\s*(\S+(?: \S+)?) [^\n]*\$\{HOST_DATA_DIR[:?]*\}', body, re.M)
    assert len(touches) >= 3, f"expected several HOST_DATA_DIR uses, found {touches}"

    mutating = [t for t in touches if t.split()[0] in {"rm", "tar"}]
    assert not mutating, (
        f"these write into HOST_DATA_DIR without the namespace helper: {mutating}. "
        "Use `inside_namespace rm ...` / `inside_namespace tar ...`."
    )
    assert "inside_namespace tar -xzf" in body
    assert "inside_namespace rm -rf" in body


def _substrate_script(name: str) -> Path:
    from podpack import substrate

    return Path(substrate.__file__).parent / "data" / "scripts" / name


def _run_restore_env_block(tmp_path: Path, existing_env: str | None) -> str:
    """Run restore.sh's .env/secrets.env handling, and nothing else.

    Lifted out of the shipped script between two stable landmarks rather than
    asserted as text: the bug this guards was a *behaviour* -- the file the
    script left behind -- and reading the source is what missed it for as long
    as it existed. The block ends by sourcing `.env`, so the values echoed
    afterwards are the ones every later compose command would use.
    """
    body = _restore_script()
    start = body.index('suffix="superseded-')
    end = body.index("set -a; . ./.env; set +a") + len("set -a; . ./.env; set +a")

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "env").write_text(
        "SITE_NAME=holdenweb-com\nWEB_HOST_PORT=3427\nPODPACK_PROXY_HOPS=1\n"
    )
    # A shape, not a credential: the test asserts nothing about the contents,
    # and a restore only ever copies this file.
    (backup / "secrets.env").write_text("POSTGRES_PASSWORD=test-scratch\n")

    site = tmp_path / "site"
    site.mkdir()
    if existing_env is not None:
        (site / ".env").write_text(existing_env)

    script = tmp_path / "block.sh"
    script.write_text(
        "set -euo pipefail\n"
        'backup="$1"\n'
        + body[start:end]
        + '\necho "PROJECT=${SITE_NAME}"\necho "PORT=${WEB_HOST_PORT}"\n'
    )
    import subprocess

    return subprocess.run(
        ["bash", str(script), str(backup)],
        cwd=site,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_a_restore_keeps_the_host_s_own_env(tmp_path: Path) -> None:
    """The value of `.env` belongs to the host, and a restore must not import it.

    `.env` carries SITE_NAME, which *is* the compose project name. Installing
    the backup's over the host's handed every later compose command in the
    directory the backup's project: measured on a real host, a restore into a
    checkout called `holdenweb-staging` drove containers called `holdenweb-com`,
    and a `compose up -d` from there would have recreated production. The old
    script's own comment said an existing `.env` "wants keeping and editing
    instead", and the code beneath it did the reverse.
    """
    out = _run_restore_env_block(
        tmp_path, "SITE_NAME=holdenweb-staging\nWEB_HOST_PORT=15055\n"
    )

    assert "PROJECT=holdenweb-staging" in out, out
    assert "PROJECT=holdenweb-com" not in out
    # The old host's port is what made the script's own closing health check
    # fail on exactly the occasion it exists for -- a move to a new host.
    assert "PORT=15055" in out, out
    # Kept where it can be read and compared, rather than discarded.
    assert (tmp_path / "site" / ".env.from-backup").exists()
    assert "SITE_NAME: keeping" in out


def test_a_fresh_clone_takes_the_backup_s_env_and_is_told_what_is_in_it(
    tmp_path: Path,
) -> None:
    """The disaster case: no `.env` to keep, so the backup's is all there is.

    It is installed, because a site with no `.env` cannot start -- but every
    value in it that describes the machine the backup came from is named, since
    each one is wrong until somebody looks.
    """
    out = _run_restore_env_block(tmp_path, existing_env=None)

    assert "PROJECT=holdenweb-com" in out, out
    for key in ("SITE_NAME", "WEB_HOST_PORT", "HOST_DATA_DIR", "PODPACK_PROXY_HOPS"):
        assert key in out, f"{key} was installed without being named"
    assert not (tmp_path / "site" / ".env.from-backup").exists()


def _backup_dir(root: Path, stamp: str, user_rows: int, source: str) -> Path:
    """A backup with just enough in it for verify-backup.sh to read it through."""
    import json
    import subprocess
    import tarfile

    d = root / stamp
    d.mkdir(parents=True)
    (d / "rowcounts.txt").write_text(
        "app|alembic_version|1\napp|role|1\napp|roles_users|1\n"
        f"app|user|{user_rows}\n"
    )
    (d / "manifest.txt").write_text(
        f"taken:            {stamp}\n"
        "from host:        opal17\n"
        f"source directory: {source}\n"
        "git commit:       0123456789abcdef\n"
        "alembic revision: a1b2c3d4\n"
        "services:         (none)\n"
    )
    # No services, so the loop that needs a running stack is skipped entirely
    # and the script can be driven for real without one.
    (d / "plan.json").write_text(
        json.dumps({"services": [], "apps": [{"name": "pages", "data": True}]})
    )
    (d / "secrets.env").write_text("POSTGRES_PASSWORD=test-scratch\n")
    (d / "env").write_text("SITE_NAME=scratch\n")
    payload = root / f".payload-{stamp}"
    payload.mkdir()
    (payload / "f").write_text("x")
    with tarfile.open(d / "app-data.tar.gz", "w:gz") as tar:
        tar.add(payload, arcname=".")
    subprocess.run(["true"], check=True)
    return d


def _run_verify(tmp_path: Path, *args: str) -> str:
    import shutil
    import subprocess

    site = tmp_path / "site"
    (site / "scripts").mkdir(parents=True)
    shutil.copy(_substrate_script("verify-backup.sh"), site / "scripts")
    root = site / "backups" / "scratch"
    (site / ".env").write_text(f"SITE_NAME=scratch\nBACKUP_ROOT={root}\n")
    return subprocess.run(
        ["bash", "scripts/verify-backup.sh", *args],
        cwd=site,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_a_backup_holding_less_than_the_one_before_it_is_remarked_on(
    tmp_path: Path,
) -> None:
    """The failed-rehearsal backup, and why nothing could tell it apart.

    A restore aborts, somebody brings the site up to look at it, podpack seeds
    `pages` from what the apps ship, and a backup is taken. Measured on a real
    host: 1036570 bytes against 1036573, five rows against four, and both
    VERIFIED. Because it is newer it becomes what this script reaches for with
    no argument, and what anybody asking for "the latest backup" gets.

    A fall is a remark and not a failure -- rows are deleted legitimately, and a
    check that cried wolf here is one people learn to skip.
    """
    site = tmp_path / "site"
    root = site / "backups" / "scratch"
    _backup_dir(root, "20260824T100000Z", 2, "/home/sholden/apps/holdenweb-com")
    _backup_dir(root, "20260824T110000Z", 1, "/tmp/restore-rehearsal")

    out = _run_verify(tmp_path)

    assert "20260824T110000Z" in out, "the newest should be the one chosen"
    assert "app.user  2 -> 1" in out, out
    assert "seeded rather than restored" in out
    assert "VERIFIED" in out, "a fall is a remark, not a failure"


def test_a_backup_that_has_grown_says_so_without_alarm(tmp_path: Path) -> None:
    site = tmp_path / "site"
    root = site / "backups" / "scratch"
    _backup_dir(root, "20260824T100000Z", 2, "/home/sholden/apps/holdenweb-com")
    _backup_dir(root, "20260824T110000Z", 9, "/home/sholden/apps/holdenweb-com")

    out = _run_verify(tmp_path)

    assert "no table has fewer rows" in out, out
    assert "NOTE" not in out


def test_the_summary_names_where_the_backup_came_from(tmp_path: Path) -> None:
    """`source directory:` is the one recorded field that separated the two.

    It was in every manifest all along and the closing summary's regex left it
    out, so the fact that a backup had been taken from a rehearsal clone was
    written down and never shown.
    """
    site = tmp_path / "site"
    root = site / "backups" / "scratch"
    _backup_dir(root, "20260824T100000Z", 2, "/tmp/restore-rehearsal")

    out = _run_verify(tmp_path)

    assert "source directory: /tmp/restore-rehearsal" in out, out
    assert "from host:        opal17" in out, out


def _backup_script() -> str:
    return _substrate_script("backup.sh").read_text()


def _run_tree_guard(tmp_path: Path, destination: str) -> tuple[int, str]:
    """Run backup.sh's inside-the-tree refusal, and nothing else.

    Lifted between landmarks and executed rather than read, because what this
    guard *says* and what it *does* were different things for as long as it
    existed.
    """
    import subprocess

    body = _backup_script()
    start = body.index("resolve() {")
    end = body.index("esac", start) + len("esac")

    site = tmp_path / "site"
    site.mkdir()
    script = tmp_path / "guard.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'here="{site}"\n'
        f'root="{tmp_path}/backups"\n'
        + body[start:end]
        + '\necho ALLOWED\n'
    )
    done = subprocess.run(
        ["bash", str(script), destination], cwd=site, capture_output=True, text=True
    )
    return done.returncode, done.stdout + done.stderr


def test_a_destination_inside_the_tree_is_refused_before_it_exists(
    tmp_path: Path,
) -> None:
    """The guard used to fire only *after* the damage it prevents.

    It resolved the destination with `$(cd "$(dirname ...)" && pwd)`. When the
    parent did not exist the `cd` failed, `&& pwd` never ran, the substitution
    was empty and the case word became `/` -- which matches nothing, so the
    backup went ahead and wrote a verbatim secrets.env into the working tree.
    `set -e` does not catch it: a command substitution that fails inside a
    `case` word is not a checked command.

    Demonstrated while this was written: the first run into a missing directory
    reported success, and the second run of the identical command -- the parent
    now existing -- refused. Rehearsal 1 printed `cd: /home/sholden/backups: No
    such file or directory` and carried on beneath it.
    """
    code, out = _run_tree_guard(tmp_path, str(tmp_path / "site" / "backups" / "demo"))

    assert code == 1, out
    assert "refusing to write a backup inside the site directory" in out
    assert "ALLOWED" not in out


def test_the_site_directory_itself_is_refused(tmp_path: Path) -> None:
    """The same line was also off by one level.

    It resolved `dirname` of the destination rather than the destination, so an
    absolute path naming the site directory resolved to the site's *parent*,
    matched nothing, and was allowed -- putting the backup directly in the tree.
    """
    code, out = _run_tree_guard(tmp_path, str(tmp_path / "site"))

    assert code == 1, out
    assert "ALLOWED" not in out


def test_a_destination_outside_the_tree_is_still_allowed(tmp_path: Path) -> None:
    """The guard has to stay usable: BACKUP_ROOT's default does not exist either."""
    code, out = _run_tree_guard(tmp_path, str(tmp_path / "backups" / "holdenweb-com"))

    assert code == 0, out
    assert "ALLOWED" in out


def test_the_cross_deployment_guard_reconstructs_no_names() -> None:
    """It was inert on every site whose name did not begin `holdenweb`.

    It looked for a mount whose destination was `/var/lib/${project%%-*}/apps`
    -- a container path rebuilt from the site name, while compose.yaml hardcodes
    `/var/lib/holdenweb/apps` for every site (item 18). And it rebuilt the
    container name as `${project}-web-1`, which compose normalises (dots
    stripped, dashes kept), so a dotted site name found no container at all and
    the `-n` test passed in silence.

    Measured: of holdenweb-com, holdenweb.com and mysite, only the first names a
    container podman can find.
    """
    # Comments stripped first. The comment above this guard explains the old
    # bug, so it necessarily contains the pattern being searched for -- the same
    # way test_the_shipped_env_never_writes_the_url_into_the_ini once failed on
    # its own documentation.
    body = "\n".join(
        line
        for line in _backup_script().splitlines()
        if not line.lstrip().startswith("#")
    )

    # Guard the search before trusting it: `project` must still be a live
    # variable in this script, or the assertions below prove nothing.
    assert 'project="${SITE_NAME:-podpack}"' in body

    assert "${project%%-*}" not in body, (
        "the guard is rebuilding a container path from the site name again"
    )
    assert '"${project}-web-1"' not in body, (
        "the guard is rebuilding a container name from the site name again"
    )
    # Asked of compose, which resolves its own project, rather than reconstructed.
    assert 'ps -q web' in body
