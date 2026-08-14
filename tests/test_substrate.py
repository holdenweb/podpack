"""What the substrate CLI promises: install, adopt, upgrade, never clobber.

These build throwaway site directories under tmp_path and drive either the
engine (when a test needs to inject a modified upstream) or the console
entry point (when the exit code or the derivation logic is the thing under
test). The canonical source tree is copied per-test when it must be
mutated, so the installed package is never touched.
"""

import json
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


def all_verbs(actions: list[Action], target: str) -> list[str]:
    """Every verb reported for one target: a file can draw more than one."""
    return [action.verb for action in actions if action.target == target]


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
    """A hand-copied substrate baselines silently; contents are not touched."""
    params = Parameters.build("my_site")
    for entry in MANIFEST:
        target = site / entry.target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(render(entry, params, DATA_ROOT))
    before = {entry.target: (site / entry.target).stat().st_mtime_ns for entry in MANIFEST}

    actions, state = plan_init(site, params, DATA_ROOT)
    substrate.apply(actions, site)

    managed = [e for e in MANIFEST if e.kind in substrate.MANAGED_KINDS]
    reported: dict[str, list[str]] = {action.target: [] for action in actions}
    for action in actions:
        reported[action.target].append(action.verb)
    assert all("adopted" in reported[e.target] for e in managed)
    after = {entry.target: (site / entry.target).stat().st_mtime_ns for entry in MANIFEST}
    assert before == after


def test_adoption_restores_a_lost_executable_bit(site: Path) -> None:
    """Byte-identical but not runnable is still broken, and `ok` for ever."""
    params = Parameters.build("my_site")
    for entry in MANIFEST:
        target = site / entry.target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(render(entry, params, DATA_ROOT))   # no exec bits
    assert not os.access(site / "scripts" / "up.sh", os.X_OK)

    actions, state = plan_init(site, params, DATA_ROOT)
    substrate.apply(actions, site)

    assert os.access(site / "scripts" / "up.sh", os.X_OK)
    assert (site / "scripts" / "up.sh").read_bytes() == render(
        substrate._entry_for("scripts/up.sh"), params, DATA_ROOT
    )


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


def test_an_unresolved_conflict_blocks_the_recorded_version(
    site: Path, upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version must not advance while work is pending.

    Asserting it equals the installed version proves nothing when init
    recorded that very version, so the installed one is moved first.
    """
    initialise(site)
    recorded_at_init = state_of(site).podpack_version
    edited = site / "scripts" / "up.sh"
    edited.write_text(edited.read_text() + "# mine\n")
    (upstream / "scripts" / "up.sh").write_text("# upstream\n")
    monkeypatch.setattr(substrate, "podpack_version", lambda: "9.9.9")

    actions, state, conflicts = plan_upgrade(site, state_of(site), upstream)
    state.save(site)
    assert conflicts == 1
    assert state_of(site).podpack_version == recorded_at_init != "9.9.9"

    # Resolving it lets the version move.
    actions, state, conflicts = plan_upgrade(
        site, state_of(site), upstream, take_upstream={"scripts/up.sh"}
    )
    substrate.apply(actions, site)
    state.save(site)
    assert conflicts == 0
    assert state_of(site).podpack_version == "9.9.9"


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


def test_a_resolution_flag_only_resolves_a_conflict(site: Path, upstream: Path) -> None:
    """Naming an unconflicted file used to act anyway, two ways round.

    `--keep` on a clean file recorded the new baseline without writing, so the
    upstream change was swallowed for ever; `--take-upstream` overwrote a site
    edit nobody had been asked about.
    """
    initialise(site)
    (upstream / "scripts" / "up.sh").write_text("# newer upstream\n")   # clean, updatable
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text() + "# mine\n")                  # edited, no upstream change

    actions, state, conflicts = plan_upgrade(
        site, state_of(site), upstream,
        keep={"scripts/up.sh"}, take_upstream={"compose.yaml"},
    )
    substrate.apply(actions, site)
    state.save(site)

    assert conflicts == 0
    # --keep on an unmodified file has nothing to resolve, and saying so is
    # what stops the upstream fix being frozen out.
    assert all_verbs(actions, "scripts/up.sh") == ["flag ignored", "updated"]
    assert (site / "scripts" / "up.sh").read_text() == "# newer upstream\n"
    # --take-upstream on a locally edited file IS meaningful: the site is
    # explicitly asking to discard its edit, which is how a copy adopted with
    # local differences converges.
    assert all_verbs(actions, "compose.yaml") == ["took upstream"]
    assert not edited.read_text().endswith("# mine\n")


def test_take_upstream_discards_an_edit_only_when_asked(site: Path) -> None:
    """Left unnamed, the same edit survives every upgrade."""
    initialise(site)
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text() + "# mine\n")

    actions, state, _ = plan_upgrade(site, state_of(site), DATA_ROOT)
    substrate.apply(actions, site)
    assert verbs(actions)["compose.yaml"] == "locally edited (kept)"
    assert edited.read_text().endswith("# mine\n")


def test_a_symlinked_target_is_left_alone(site: Path, upstream: Path, tmp_path: Path) -> None:
    """Writing would follow the link and land outside the site entirely."""
    initialise(site)
    outside = tmp_path / "elsewhere.conf"
    outside.write_text("not podpack's to touch\n")
    linked = site / "config" / "postgresql.conf"
    linked.unlink()
    linked.symlink_to(outside)
    (upstream / "config" / "postgresql.conf").write_text("# newer\n")

    actions, state, conflicts = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert verbs(actions)["config/postgresql.conf"] == "not managed here"
    assert outside.read_text() == "not podpack's to touch\n"


def test_a_symlinked_parent_directory_cannot_be_written_through(
    site: Path, upstream: Path, tmp_path: Path
) -> None:
    """The leaf is an ordinary path; only resolving the whole of it helps.

    Pointing `scripts/` or `config/` at a shared checkout is an ordinary
    arrangement, and a guard that asks the leaf whether it is a symlink sees
    nothing wrong with it.
    """
    shared = tmp_path / "shared-ops"
    shared.mkdir()
    (shared / "up.sh").write_text("# the shared copy, not podpack's\n")
    (shared / "prepare-host-dirs.sh").write_text("# also shared\n")
    initialise(site)
    shutil.rmtree(site / "scripts")
    (site / "scripts").symlink_to(shared)
    (upstream / "scripts" / "up.sh").write_text("# newer upstream\n")

    for flags in ({}, {"take_upstream": {"scripts/up.sh"}}):
        actions, state, conflicts = plan_upgrade(site, state_of(site), upstream, **flags)
        substrate.apply(actions, site)
        assert verbs(actions)["scripts/up.sh"] == "not managed here"

    assert (shared / "up.sh").read_text() == "# the shared copy, not podpack's\n"
    assert (shared / "prepare-host-dirs.sh").read_text() == "# also shared\n"


def test_a_conflict_copy_cannot_be_planted_outside_the_site(
    site: Path, upstream: Path, tmp_path: Path
) -> None:
    """`<file>.new` is a write like any other, and was the one without a guard."""
    initialise(site)
    outside = tmp_path / "notes.yaml"
    outside.write_text("mine\n")
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text() + "# mine\n")
    (upstream / "compose.yaml").write_text("# upstream\n")
    (site / "compose.yaml.new").symlink_to(outside)

    actions, state, conflicts = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert conflicts == 1
    assert outside.read_text() == "mine\n"


def test_init_does_not_chmod_through_a_link(site: Path, tmp_path: Path) -> None:
    """The first command a site runs must not change a mode outside it."""
    params = Parameters.build("my_site")
    outside = tmp_path / "up.sh"
    outside.write_bytes(render(substrate._entry_for("scripts/up.sh"), params, DATA_ROOT))
    outside.chmod(0o644)
    (site / "scripts").mkdir()
    (site / "scripts" / "up.sh").symlink_to(outside)

    initialise(site)

    assert not os.access(outside, os.X_OK)


def test_config_delivery_cannot_append_outside_the_site(
    site: Path, upstream: Path, tmp_path: Path
) -> None:
    initialise(site)
    outside = tmp_path / "env.example"
    outside.write_text("SITE_NAME=mine\n")
    (site / ".env.example").unlink()
    (site / ".env.example").symlink_to(outside)
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nNEW_KNOB=1\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert outside.read_text() == "SITE_NAME=mine\n"


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


def test_contradictory_resolution_flags_are_a_usage_error(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Branch order would decide it, and the loser is the site's own copy."""
    initialise(site)
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text() + "# mine\n")
    assert main(["substrate", "upgrade", "--dir", str(site),
                 "--take-upstream", "compose.yaml", "--keep", "compose.yaml"]) == 2
    assert "both" in capsys.readouterr().out
    assert edited.read_text().endswith("# mine\n")


def test_take_upstream_keeps_the_discarded_version_beside_it(site: Path) -> None:
    """"Nothing is ever clobbered" has to survive the one destructive flag."""
    initialise(site)
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text() + "# my production tweak\n")
    mine = edited.read_text()

    actions, state, _ = plan_upgrade(
        site, state_of(site), DATA_ROOT, take_upstream={"compose.yaml"}
    )
    substrate.apply(actions, site)

    assert not edited.read_text().endswith("# my production tweak\n")
    assert (site / "compose.yaml.orig").read_text() == mine


def test_a_hand_merged_conflict_copy_is_not_deleted(site: Path, upstream: Path) -> None:
    """The conflict message invites merging into .new; deleting only what
    podpack wrote keeps that work."""
    initialise(site)
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text() + "# mine\n")
    (upstream / "compose.yaml").write_text("# upstream\n")
    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)
    (site / "compose.yaml.new").write_text("MY CAREFUL HAND MERGE\n")

    actions, state, _ = plan_upgrade(
        site, state_of(site), upstream, take_upstream={"compose.yaml"}
    )
    substrate.apply(actions, site)

    assert (site / "compose.yaml.new").read_text() == "MY CAREFUL HAND MERGE\n"


def test_a_conflict_resolved_by_hand_clears_its_artifact(site: Path, upstream: Path) -> None:
    """Copying the .new over is the obvious fix, and left it behind to rot."""
    initialise(site)
    edited = site / "compose.yaml"
    edited.write_text(edited.read_text() + "# mine\n")
    (upstream / "compose.yaml").write_text("# upstream\n")
    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)

    shutil.copy(site / "compose.yaml.new", site / "compose.yaml")   # the hand fix
    actions, state, conflicts = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert conflicts == 0
    assert not (site / "compose.yaml.new").exists()


def test_an_unrecorded_parameter_is_not_re_derived(site: Path, upstream: Path) -> None:
    """A state file that predates a parameter says nothing about it, and
    inventing 8458 there writes a plausible wrong value into a live file."""
    assert main(["substrate", "init", "--dir", str(site), "--yes",
                 "--web-port", "9000"]) == 0
    raw = json.loads((site / "substrate.json").read_text())
    del raw["parameters"]["web_port"]                    # an older state file
    (site / "substrate.json").write_text(json.dumps(raw))
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nPROBE=http://localhost:@@WEB_HOST_PORT@@/x\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)

    assert "PROBE=http://localhost:CHANGEME/x" in (site / ".env.example").read_text()


def test_a_damaged_state_file_exits_two_not_one(site: Path) -> None:
    """Exit 1 means "conflicts to resolve"; a CI gate must tell them apart."""
    initialise(site)
    raw = json.loads((site / "substrate.json").read_text())
    del raw["files"]
    (site / "substrate.json").write_text(json.dumps(raw))
    for command in ("upgrade", "status", "diff"):
        assert main(["substrate", command, "--dir", str(site)]) == 2


def test_status_does_not_invent_pending_work_for_a_trimmed_env(
    site: Path, upstream: Path
) -> None:
    """status must seed a first-sighted .env exactly as upgrade does."""
    initialise(site)
    (site / ".env").write_text("SITE_NAME=my-site\n")   # trimmed, created after init

    lines, pending = substrate.status(site, state_of(site), upstream)
    actions, _, _ = plan_upgrade(site, state_of(site), upstream)

    assert not pending
    assert not any(a.verb == "appended new variables" for a in actions)


def test_a_delivery_does_not_clear_an_unrelated_drift_report(
    site: Path, upstream: Path
) -> None:
    initialise(site)
    example = site / ".env.example"
    example.write_text(example.read_text() + "MY_OWN=1\n")     # the site's edit
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nNEW_KNOB=1\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)

    lines, _ = substrate.status(site, state_of(site), upstream)
    reported = next(line for line in lines if line.startswith(".env.example "))
    assert "since edited" in reported


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


def test_podpacks_own_append_is_not_reported_as_a_site_edit(
    site: Path, upstream: Path
) -> None:
    initialise(site)
    with (upstream / "env.example").open("a") as stream:
        stream.write("\nNEW_KNOB=1\n")

    actions, state, _ = plan_upgrade(site, state_of(site), upstream)
    substrate.apply(actions, site)
    state.save(site)

    lines, _ = substrate.status(site, state_of(site), upstream)
    reported = next(line for line in lines if line.startswith(".env.example "))
    assert "since edited" not in reported


def secrets_line(lines: list[str]) -> str:
    return next(line for line in lines if line.startswith("secrets.env "))


def test_status_reports_a_secret_the_site_must_add(site: Path, upstream: Path) -> None:
    """`status --check` must agree with what an upgrade would tell the site."""
    initialise(site)
    shutil.copy(site / "secrets.env.example", site / "secrets.env")
    with (upstream / "secrets.env.example").open("a") as stream:
        stream.write("\nAPI_TOKEN=lab-only\n")

    lines, pending = substrate.status(site, state_of(site), upstream)
    assert pending
    assert secrets_line(lines).endswith("add by hand: API_TOKEN")


def test_a_secret_managed_elsewhere_can_be_acknowledged(site: Path, upstream: Path) -> None:
    """A commented-out entry is an answer; re-asking for ever is noise."""
    initialise(site)
    shutil.copy(site / "secrets.env.example", site / "secrets.env")
    with (site / "secrets.env").open("a") as stream:
        stream.write("# API_TOKEN= (kept in the vault)\n")
    with (upstream / "secrets.env.example").open("a") as stream:
        stream.write("\nAPI_TOKEN=lab-only\n")

    lines, _ = substrate.status(site, state_of(site), upstream)
    assert "API_TOKEN" not in secrets_line(lines)

    actions, _, _ = plan_upgrade(site, state_of(site), upstream)
    assert not any(a.target == "secrets.env" for a in actions)


def test_adoption_reports_the_variables_it_will_not_add(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Adoption treats a canon variable a site's copy lacks as its business --
    which is only visible now, so it is said out loud."""
    (site / ".env.example").write_text("SITE_NAME=mine\n")
    initialise(site)
    out = capsys.readouterr().out
    assert "not adding what it lacks" in out
    assert "GUNICORN_WORKERS" in out


def test_a_state_file_from_a_newer_schema_is_refused(site: Path) -> None:
    initialise(site)
    raw = json.loads((site / "substrate.json").read_text())
    raw["substrate_schema"] = substrate.STATE_SCHEMA + 1
    (site / "substrate.json").write_text(json.dumps(raw))
    with pytest.raises(RuntimeError, match="substrate schema"):
        State.load(site)


def test_init_refuses_a_directory_that_does_not_exist(tmp_path: Path) -> None:
    """apply() creates parents, so a mistyped --dir would plant a whole tree."""
    assert main(["substrate", "init", "--dir", str(tmp_path / "typo"), "--yes"]) == 2
    assert not (tmp_path / "typo").exists()


def test_the_site_package_is_derived_as_uv_build_would(tmp_path: Path) -> None:
    """`My-Site` builds src/my_site/, so anything else names a module that
    gunicorn cannot import."""
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "pyproject.toml").write_text('[project]\nname = "My-Site"\nversion = "0"\n')
    assert main(["substrate", "init", "--dir", str(site_dir), "--yes"]) == 0
    assert "'my_site:create_app()'" in (site_dir / "Containerfile").read_text()


def test_the_shipped_python_still_compiles() -> None:
    """The substrate's own .py files are data here and code in a site.

    They are outside mypy's reach by design, so this is the floor: a site's
    alembic environment that does not even parse is exactly the failure
    holdenweb.com shipped for months in a file nothing imported.
    """
    for entry in MANIFEST:
        if not entry.source.endswith(".py"):
            continue
        source = (DATA_ROOT / entry.source).read_text()
        compile(source, entry.source, "exec")


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


def test_every_manifest_source_ships_in_a_built_wheel(tmp_path: Path) -> None:
    """The wheel, not the editable install this suite otherwise resolves.

    `test_every_manifest_source_ships_with_the_package` reads through
    `source_root()`, which in a development checkout is the source tree --
    so it would pass with the whole substrate excluded from packaging, and
    the failure would first appear to somebody who had installed podpack
    from an index. Building is slow enough to skip when the tools are not
    there, and cheap enough to be worth it when they are.
    """
    import subprocess
    import zipfile

    build = subprocess.run(
        ["uv", "build", "--wheel", "--quiet", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"uv build unavailable: {build.stderr.strip()[:80]}")

    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "uv build produced no wheel"
    with zipfile.ZipFile(wheels[0]) as wheel:
        shipped = set(wheel.namelist())

    for entry in MANIFEST:
        assert f"podpack/substrate/data/{entry.source}" in shipped, entry.source
    assert "podpack/py.typed" in shipped
