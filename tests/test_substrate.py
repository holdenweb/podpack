"""What the substrate CLI promises: install, adopt, upgrade, never clobber.

These build throwaway site directories under tmp_path and drive either the
engine (when a test needs to inject a modified upstream) or the console
entry point (when the exit code or the derivation logic is the thing under
test). The canonical source tree is copied per-test when it must be
mutated, so the installed package is never touched.
"""

import os
import shutil
import stat
from pathlib import Path

import pytest

from podpack import substrate
from podpack.cli import main
from podpack.substrate import (
    MANIFEST,
    Action,
    Parameters,
    State,
    plan_init,
    plan_upgrade,
    render,
    sha256,
)

REPO_ROOT = Path(__file__).parents[1]
DATA_ROOT = REPO_ROOT / "src" / "podpack" / "substrate" / "data"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """An empty site directory with the pyproject `init` derives names from."""
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "pyproject.toml").write_text('[project]\nname = "my-site"\nversion = "0"\n')
    return site_dir


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """A mutable copy of the canonical tree, standing in for a newer podpack."""
    root = tmp_path / "upstream"
    shutil.copytree(DATA_ROOT, root)
    return root


def initialise(site_dir: Path) -> None:
    assert main(["substrate", "init", "--dir", str(site_dir), "--yes"]) == 0


def state_of(site_dir: Path) -> State:
    state = State.load(site_dir)
    assert state is not None
    return state


def verbs(actions: list[Action]) -> dict[str, str]:
    return {action.target: action.verb for action in actions}


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_lays_down_a_complete_site(site: Path) -> None:
    initialise(site)
    for entry in MANIFEST:
        assert (site / entry.target).is_file(), entry.target
    # The one render: the site's factory in the gunicorn CMD.
    assert "'my_site:create_app()'" in (site / "Containerfile").read_text()
    # No token survives anywhere -- a leftover @@ is always a bug.
    for entry in MANIFEST:
        assert b"@@" not in (site / entry.target).read_bytes(), entry.target
    # Scripts arrive executable.
    for name in ("scripts/up.sh", "scripts/prepare-host-dirs.sh", "db-init/01-create-app-user.sh"):
        assert os.access(site / name, os.X_OK), name
    # The recorded baselines are the hashes of what is on disk.
    state = state_of(site)
    for entry in MANIFEST:
        assert state.files[entry.target]["sha256"] == sha256((site / entry.target).read_bytes())


def test_init_derives_the_site_package_the_way_uv_build_does(site: Path) -> None:
    initialise(site)
    assert state_of(site).parameters["site_package"] == "my_site"
    assert state_of(site).parameters["site_name"] == "my-site"


def test_init_refuses_to_run_twice(site: Path) -> None:
    initialise(site)
    assert main(["substrate", "init", "--dir", str(site), "--yes"]) == 2


def test_init_adopts_matching_files_without_rewriting_them(site: Path) -> None:
    """A hand-copied substrate baselines silently; nothing is touched."""
    params = Parameters.build("my_site")
    for entry in MANIFEST:
        target = site / entry.target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(render(entry, params, DATA_ROOT))
    before = {entry.target: (site / entry.target).stat().st_mtime_ns for entry in MANIFEST}

    actions, state = plan_init(site, params, DATA_ROOT)
    substrate.apply(actions, site)

    managed = [e for e in MANIFEST if e.kind in substrate.MANAGED_KINDS]
    assert all(verbs(actions)[e.target] == "adopted" for e in managed)
    after = {entry.target: (site / entry.target).stat().st_mtime_ns for entry in MANIFEST}
    assert before == after


def test_init_keeps_a_locally_edited_file_and_records_the_canonical_hash(site: Path) -> None:
    """The baseline is what podpack rendered, so the edit stays visible for ever."""
    initialise(site)
    (site / "substrate.json").unlink()  # simulate adopting a pre-CLI site...
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text().replace(":-podpack", ":-my-site"))

    params = Parameters.build("my_site")
    actions, state = plan_init(site, params, DATA_ROOT)
    substrate.apply(actions, site)

    assert verbs(actions)["compose.yaml"] == "kept local version"
    assert ":-my-site" in edited.read_text()  # untouched
    canonical = render(substrate._entry_for("compose.yaml"), params, DATA_ROOT)
    assert state.files["compose.yaml"]["sha256"] == sha256(canonical)


# ---------------------------------------------------------------------------
# upgrade: the three-way rules
# ---------------------------------------------------------------------------


def test_upgrade_applies_an_upstream_fix_to_a_clean_file(site: Path, upstream: Path) -> None:
    """The podpack-demo case in miniature: a bug fix reaches an untouched copy."""
    initialise(site)
    fixed = (upstream / "scripts" / "up.sh").read_text().replace(
        "exec podman compose", "exec podman compose --fixed"
    )
    (upstream / "scripts" / "up.sh").write_text(fixed)

    actions, state, conflicts = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert conflicts == 0
    assert verbs(actions)["scripts/up.sh"] == "updated"
    assert "--fixed" in (site / "scripts" / "up.sh").read_text()
    assert state.files["scripts/up.sh"]["sha256"] == sha256(fixed.encode())
    # The updated script is still executable.
    assert os.access(site / "scripts" / "up.sh", os.X_OK)


def test_upgrade_leaves_a_site_edit_alone_when_upstream_is_unchanged(site: Path) -> None:
    initialise(site)
    edited = site / "scripts" / "up.sh"
    original_baseline = state_of(site).files["scripts/up.sh"]["sha256"]
    edited.write_text(edited.read_text() + "# mine\n")

    actions, state, conflicts = plan_upgrade(site, state_of(site), DATA_ROOT)
    substrate.apply(actions, site)

    assert conflicts == 0
    assert verbs(actions)["scripts/up.sh"] == "locally edited (kept)"
    assert edited.read_text().endswith("# mine\n")
    assert state.files["scripts/up.sh"]["sha256"] == original_baseline


def test_a_conflict_writes_dot_new_and_clobbers_nothing(site: Path, upstream: Path) -> None:
    initialise(site)
    edited = site / "scripts" / "up.sh"
    edited.write_text(edited.read_text() + "# mine\n")
    (upstream / "scripts" / "up.sh").write_text(
        (upstream / "scripts" / "up.sh").read_text() + "# upstream\n"
    )

    for _ in range(2):  # idempotent: a re-run reports the same conflict
        actions, state, conflicts = plan_upgrade(site, state_of(site), upstream)
        substrate.apply(actions, site)
        state.save(site)
        assert conflicts == 1
        assert verbs(actions)["scripts/up.sh"] == "conflict"

    assert edited.read_text().endswith("# mine\n")  # the site's copy untouched
    assert (site / "scripts" / "up.sh.new").read_text().endswith("# upstream\n")
    # An unresolved conflict blocks convergence: the recorded version stays put.
    assert state_of(site).podpack_version == substrate.podpack_version()


def test_take_upstream_and_keep_resolve_a_conflict_each_way(site: Path, upstream: Path) -> None:
    initialise(site)
    for name in ("scripts/up.sh", "compose.yaml"):
        (site / name).write_text((site / name).read_text() + "# mine\n")
        (upstream / substrate._entry_for(name).source).write_text(
            (upstream / substrate._entry_for(name).source).read_text() + "# upstream\n"
        )
    actions, state, conflicts = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)
    assert conflicts == 2

    actions, state, conflicts = plan_upgrade(
        site, state_of(site), upstream,
        take_upstream={"scripts/up.sh"}, keep={"compose.yaml"},
    )
    substrate.apply(actions, site)
    state.save(site)

    assert conflicts == 0
    assert (site / "scripts" / "up.sh").read_text().endswith("# upstream\n")
    assert not (site / "scripts" / "up.sh.new").exists()
    assert (site / "compose.yaml").read_text().endswith("# mine\n")
    # A resolved conflict leaves no artifact either way round: a .new kept
    # after --keep would sit there going quietly stale.
    assert not (site / "compose.yaml.new").exists()
    # Both baselines now acknowledge the upstream version...
    upstream_compose = (upstream / "compose.yaml").read_bytes()
    assert state.files["compose.yaml"]["sha256"] == sha256(upstream_compose)
    # ...so the next upgrade is quiet.
    actions, _, conflicts = plan_upgrade(site, state_of(site), upstream)
    assert conflicts == 0
    assert verbs(actions)["compose.yaml"] == "locally edited (kept)"


def test_a_deleted_managed_file_is_restored(site: Path) -> None:
    initialise(site)
    (site / "container" / "healthcheck.py").unlink()
    actions, _, conflicts = plan_upgrade(site, state_of(site), DATA_ROOT)
    substrate.apply(actions, site)
    assert conflicts == 0
    assert verbs(actions)["container/healthcheck.py"] == "restored"
    assert (site / "container" / "healthcheck.py").is_file()


# ---------------------------------------------------------------------------
# configuration: append-only delivery
# ---------------------------------------------------------------------------


def test_seeded_files_are_never_rewritten(site: Path, upstream: Path) -> None:
    initialise(site)
    (site / ".gitignore").write_text("# entirely mine now\n")
    (upstream / "gitignore").write_text("# a better upstream gitignore\n")

    actions, _, conflicts = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert conflicts == 0
    assert (site / ".gitignore").read_text() == "# entirely mine now\n"


def test_a_new_env_var_is_appended_once_and_not_readded_after_deletion(
    site: Path, upstream: Path
) -> None:
    initialise(site)
    with (upstream / "env.example").open("a") as stream:
        stream.write("\n# How chatty the site is.\nLOG_LEVEL=info\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)

    text = (site / ".env.example").read_text()
    assert "LOG_LEVEL=info" in text
    assert "# How chatty the site is." in text
    assert "added by podpack substrate upgrade" in text

    # The site deletes it; delivery never pushes it back.
    (site / ".env.example").write_text(text.replace("LOG_LEVEL=info\n", ""))
    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)
    assert "LOG_LEVEL" not in (site / ".env.example").read_text()


def test_existing_lines_survive_variable_delivery(site: Path, upstream: Path) -> None:
    """Append-only means the site's own content is byte-preserved."""
    initialise(site)
    mine = "# my own notes\nSITE_NAME=very-custom\n"
    (site / ".env.example").write_text(mine)
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nNEW_KNOB=1\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    text = (site / ".env.example").read_text()
    assert text.startswith(mine)  # untouched prefix
    assert "NEW_KNOB=1" in text


def test_a_live_env_receives_new_variables_too(site: Path, upstream: Path) -> None:
    (site / ".env").write_text("SITE_NAME=my-site\n")
    initialise(site)
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nNEW_KNOB=1\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    text = (site / ".env").read_text()
    assert text.startswith("SITE_NAME=my-site\n")
    assert "NEW_KNOB=1" in text


def test_an_env_created_after_init_is_still_delivered_to(site: Path, upstream: Path) -> None:
    """The documented order: init writes .env.example, the site copies it after.

    Tracking only what init saw meant a site following the guide never
    received a new variable -- silently, and for ever.
    """
    initialise(site)
    shutil.copy(site / ".env.example", site / ".env")
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nNEW_KNOB=1\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)

    assert "NEW_KNOB=1" in (site / ".env").read_text()
    # And exactly once: a second upgrade has nothing left to deliver.
    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    assert (site / ".env").read_text().count("NEW_KNOB=1") == 1


def test_status_reports_a_live_envs_pending_variables(site: Path, upstream: Path) -> None:
    initialise(site)
    shutil.copy(site / ".env.example", site / ".env")
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nNEW_KNOB=1\n")

    lines, pending = substrate.status(site, state_of(site), upstream)
    assert pending
    assert any(line.startswith(".env ") and "NEW_KNOB" in line for line in lines)


def test_a_delivered_default_uses_the_sites_own_parameters(site: Path, upstream: Path) -> None:
    """A new variable's default is rendered from what this site chose at init,
    not from re-derived lab values -- and an unrecorded parameter (the
    password) is marked rather than invented."""
    assert main(["substrate", "init", "--dir", str(site), "--yes",
                 "--web-port", "9000", "--db-user", "custom_role"]) == 0
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nMETRICS_URL=http://localhost:@@WEB_HOST_PORT@@/m?u=@@DB_USER@@\n")
        stream.write("PROBE_PASSWORD=@@DB_PASSWORD@@\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    text = (site / ".env.example").read_text()
    assert "METRICS_URL=http://localhost:9000/m?u=custom_role" in text
    assert "PROBE_PASSWORD=CHANGEME" in text


def test_secrets_env_is_never_written_and_new_secrets_are_reported(
    site: Path, upstream: Path
) -> None:
    initialise(site)
    before = "SECRET_KEY=real\n"
    (site / "secrets.env").write_text(before)
    with (upstream / "secrets.env.example").open("a") as stream:
        stream.write("\nAPI_TOKEN=lab-only\n")

    actions, _, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert (site / "secrets.env").read_text() == before
    reported = {a.target: a for a in actions}
    assert "secrets.env" in reported
    assert "API_TOKEN" in reported["secrets.env"].detail


# ---------------------------------------------------------------------------
# status / CLI exit codes
# ---------------------------------------------------------------------------


def test_status_check_exits_nonzero_when_an_upgrade_is_pending(
    site: Path, upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialise(site)
    assert main(["substrate", "status", "--dir", str(site), "--check"]) == 0

    (upstream / "scripts" / "up.sh").write_text("# newer\n")
    monkeypatch.setattr(substrate, "source_root", lambda: upstream)
    assert main(["substrate", "status", "--dir", str(site), "--check"]) == 1


def test_upgrade_exit_code_reports_conflicts(
    site: Path, upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialise(site)
    (site / "compose.yaml").write_text("mine\n")
    (upstream / "compose.yaml").write_text("upstream\n")
    monkeypatch.setattr(substrate, "source_root", lambda: upstream)
    assert main(["substrate", "upgrade", "--dir", str(site)]) == 1
    assert main(["substrate", "upgrade", "--dir", str(site),
                 "--take-upstream", "compose.yaml"]) == 0


def test_commands_refuse_an_uninitialised_site(tmp_path: Path) -> None:
    for command in ("upgrade", "status", "diff"):
        assert main(["substrate", command, "--dir", str(tmp_path)]) == 2


def test_a_mistyped_resolution_path_is_refused_rather_than_ignored(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silently matching nothing reads as 'resolution did not work' with no clue."""
    initialise(site)
    assert main(["substrate", "upgrade", "--dir", str(site),
                 "--take-upstream", "compose.yml"]) == 2   # .yml, not .yaml
    assert "compose.yml" in capsys.readouterr().out


def test_a_mistyped_diff_path_is_refused_rather_than_ignored(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silence from diff reads as 'no differences', the opposite of the truth."""
    initialise(site)
    assert main(["substrate", "diff", "--dir", str(site), "Containerfil"]) == 2
    assert "Containerfil" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the dogfood pin, and packaging
# ---------------------------------------------------------------------------


def test_the_repo_root_is_the_rendered_substrate() -> None:
    """podpack's own root is an instance of its packaged substrate.

    This is the test that keeps the canonical tree and the working lab from
    drifting apart -- the drift that motivated the whole command.
    """
    params = Parameters.build("podpack", site_name="holdenweb-lab")
    for entry in MANIFEST:
        if entry.kind not in substrate.MANAGED_KINDS:
            continue
        rendered = render(entry, params, DATA_ROOT)
        assert rendered == (REPO_ROOT / entry.target).read_bytes(), entry.target


def test_every_manifest_source_ships_with_the_package() -> None:
    """Catches a packaging exclusion before a release does."""
    root = substrate.source_root()
    for entry in MANIFEST:
        assert (root / entry.source).is_file(), entry.source


def test_no_manifest_entry_reaches_outside_the_site(tmp_path: Path) -> None:
    """Targets are relative and descend -- the engine never writes elsewhere."""
    for entry in MANIFEST:
        target = Path(entry.target)
        assert not target.is_absolute()
        assert ".." not in target.parts
