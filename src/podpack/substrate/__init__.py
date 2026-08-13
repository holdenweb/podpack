"""Install and upgrade a site's copy of the container substrate.

The canonical substrate lives in this package's `data/` directory and ships
in the wheel, so a site's locked podpack version and its substrate travel
together. `podpack substrate init` lays it down or adopts an existing copy;
`upgrade` brings a copy up to the installed version. See ADR-0026 for why
this exists and the rules it follows.

The engine walks an explicit manifest, never the filesystem: real sites keep
their own files (personal scripts, notes) beside the substrate's, and a
directory sweep could take out work git cannot restore.

The state file records, per managed file, the sha256 of **what podpack
rendered** -- never of what the site has on disk. That invariant is what
makes the three-way rules sound: a site's edit stays visible as drift for
ever, and an upstream change to a file the site edited becomes a conflict
rather than a silent clobber.

Nothing here is substrate-specific by necessity -- manifest, baselines,
three-way sync would serve an app's shipped data identically. That second
consumer is deliberately not built yet (ADR-0008's deferred gap).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from importlib.metadata import version as _distribution_version
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

STATE_FILE = "substrate.json"
STATE_SCHEMA = 1

# File classes. Managed files are podpack's code and upgrade; configuration
# is the site's once delivered and only ever grows; seeded files are written
# once and become the site's outright.
VERBATIM = "verbatim"    # managed, byte-identical
RENDERED = "rendered"    # managed, site parameters substituted
CONFIG = "config"        # seeded, then append-only parameter delivery
SEEDED = "seeded"        # written once if absent, never touched again

_TOKEN_RE = re.compile(r"@@[A-Z_]+@@")
_VAR_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")


@dataclass(frozen=True)
class SubstrateFile:
    """One manifest row: where a file comes from, lands, and how it behaves.

    Sources are stored un-dotted (`dockerignore`, `env.example`) to dodge
    hidden-file edge cases in packaging tools; the target names the real
    spelling.
    """

    source: str
    target: str
    kind: str
    executable: bool = False


MANIFEST: tuple[SubstrateFile, ...] = (
    SubstrateFile("Containerfile", "Containerfile", RENDERED),
    SubstrateFile("compose.yaml", "compose.yaml", VERBATIM),
    SubstrateFile("dockerignore", ".dockerignore", VERBATIM),
    SubstrateFile("container/healthcheck.py", "container/healthcheck.py", VERBATIM),
    SubstrateFile(
        "db-init/01-create-app-user.sh",
        "db-init/01-create-app-user.sh",
        VERBATIM,
        executable=True,
    ),
    SubstrateFile("scripts/up.sh", "scripts/up.sh", VERBATIM, executable=True),
    SubstrateFile(
        "scripts/prepare-host-dirs.sh",
        "scripts/prepare-host-dirs.sh",
        VERBATIM,
        executable=True,
    ),
    SubstrateFile("config/postgresql.conf", "config/postgresql.conf", VERBATIM),
    SubstrateFile("config/pg_hba.conf", "config/pg_hba.conf", VERBATIM),
    SubstrateFile("config/pg_ident.conf", "config/pg_ident.conf", VERBATIM),
    SubstrateFile("alembic/env.py", "alembic/env.py", VERBATIM),
    SubstrateFile("alembic/script.py.mako", "alembic/script.py.mako", VERBATIM),
    SubstrateFile("alembic.ini", "alembic.ini", VERBATIM),
    SubstrateFile("env.example", ".env.example", CONFIG),
    SubstrateFile("secrets.env.example", "secrets.env.example", CONFIG),
    SubstrateFile("gitignore", ".gitignore", SEEDED),
    SubstrateFile("README.stub.md", "README.md", SEEDED),
    SubstrateFile("alembic/versions/gitkeep", "alembic/versions/.gitkeep", SEEDED),
)

MANAGED_KINDS = (VERBATIM, RENDERED)


@dataclass(frozen=True)
class Parameters:
    """The site facts substituted into rendered files and seeds.

    Only `site_package` and `site_name` are recorded in the state file; the
    database identity is consumed by the seeds at init and never needed
    again -- and recording nothing password-shaped keeps the question of
    secrets in a committed file from ever arising.
    """

    site_package: str
    site_name: str
    web_port: int = 8458
    db_port: int = 5433
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""

    @staticmethod
    def build(site_package: str, **overrides: Any) -> "Parameters":
        """Fill the derivable defaults from the one required fact."""
        dashed = site_package.replace("_", "-")
        values: dict[str, Any] = {
            "site_name": dashed,
            "db_name": site_package,
            "db_user": f"{site_package}_app",
            "db_password": f"{dashed}-app-password",  # a lab value, like the seeds'
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return Parameters(site_package=site_package, **values)

    def tokens(self) -> dict[str, str]:
        return {
            "@@SITE_PACKAGE@@": self.site_package,
            "@@SITE_NAME@@": self.site_name,
            "@@WEB_HOST_PORT@@": str(self.web_port),
            "@@POSTGRES_HOST_PORT@@": str(self.db_port),
            "@@DB_NAME@@": self.db_name,
            "@@DB_USER@@": self.db_user,
            "@@DB_PASSWORD@@": self.db_password,
        }


@dataclass
class Action:
    """One thing init/upgrade decided about one target file."""

    target: str
    verb: str
    detail: str = ""
    content: bytes | None = None
    executable: bool = False


@dataclass
class State:
    """The committed substrate.json, as data."""

    podpack_version: str
    parameters: dict[str, str]
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    delivered_vars: dict[str, list[str]] = field(default_factory=dict)

    @staticmethod
    def load(site_dir: Path) -> "State | None":
        path = site_dir / STATE_FILE
        if not path.is_file():
            return None
        raw = json.loads(path.read_text())
        return State(
            podpack_version=raw["podpack_version"],
            parameters=raw["parameters"],
            files=raw["files"],
            delivered_vars=raw.get("delivered_vars", {}),
        )

    def save(self, site_dir: Path) -> None:
        payload = {
            "substrate_schema": STATE_SCHEMA,
            "podpack_version": self.podpack_version,
            "parameters": self.parameters,
            "files": self.files,
            "delivered_vars": self.delivered_vars,
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        (site_dir / STATE_FILE).write_text(text)


def podpack_version() -> str:
    return _distribution_version("podpack")


def source_root() -> Traversable | Path:
    return files("podpack.substrate") / "data"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render(entry: SubstrateFile, params: Parameters, root: Traversable | Path) -> bytes:
    """The canonical content of one file for one site.

    Substitution is a literal token replace: str.format and string.Template
    both collide with the `${VAR:-default}` shell syntax the files carry.
    """
    raw = (root / entry.source).read_bytes()
    if entry.kind == VERBATIM:
        return raw
    text = raw.decode("utf-8")
    for token, value in params.tokens().items():
        text = text.replace(token, value)
    return text.encode("utf-8")


def unrendered_tokens(data: bytes) -> list[str]:
    """Tokens that survived a render -- always a bug or a missing parameter."""
    return sorted(set(_TOKEN_RE.findall(data.decode("utf-8", errors="replace"))))


def env_var_names(text: str) -> list[str]:
    """The variable names an env-format file defines, in order."""
    names = []
    for line in text.splitlines():
        match = _VAR_LINE_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def env_var_blocks(text: str) -> dict[str, str]:
    """Each variable with the comment run immediately above it.

    A banner comment introducing several variables attaches to the first of
    them; the rest carry just their own line. Good enough for delivery, which
    only ever appends whole blocks.
    """
    blocks: dict[str, str] = {}
    pending: list[str] = []
    for line in text.splitlines():
        match = _VAR_LINE_RE.match(line)
        if match:
            blocks[match.group(1)] = "\n".join([*pending, line])
            pending = []
        elif line.lstrip().startswith("#"):
            pending.append(line)
        else:
            pending = []
    return blocks


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def plan_init(site_dir: Path, params: Parameters, root: Traversable | Path) -> tuple[list[Action], State]:
    """Lay down a complete substrate, or adopt an existing copy in place.

    Both audiences fall out of one rule set: on an empty site everything is
    missing and gets written; on a site that copied the substrate by hand,
    identical files baseline silently and edited ones are kept -- with the
    baseline recording the *canonical* hash, so the edit stays visible.
    """
    actions: list[Action] = []
    state = State(podpack_version=podpack_version(), parameters={
        "site_package": params.site_package,
        "site_name": params.site_name,
    })

    for entry in MANIFEST:
        target = site_dir / entry.target
        rendered = render(entry, params, root)
        record: dict[str, Any] = {"class": entry.kind, "sha256": sha256(rendered)}

        if entry.kind in MANAGED_KINDS:
            if not target.exists():
                actions.append(Action(entry.target, "write", content=rendered,
                                      executable=entry.executable))
            elif target.read_bytes() == rendered:
                actions.append(Action(entry.target, "adopted"))
            else:
                actions.append(Action(
                    entry.target, "kept local version",
                    detail=f"differs from podpack {state.podpack_version} -- "
                           f"see: podpack substrate diff {entry.target}",
                ))
        else:  # CONFIG and SEEDED: the site's file once it exists
            if not target.exists():
                actions.append(Action(entry.target, "write", content=rendered,
                                      executable=entry.executable))
                record["seeded_by"] = state.podpack_version
            else:
                actions.append(Action(entry.target, "pre-existing"))
                record["sha256"] = sha256(target.read_bytes())
                record["seeded_by"] = "pre-existing"
            if entry.kind == CONFIG:
                delivered = set(env_var_names(rendered.decode("utf-8")))
                if target.exists():
                    # Anything the site already defines counts as delivered:
                    # upgrade must never push a variable back at a site that
                    # has it, or had it and removed it.
                    delivered |= set(env_var_names(target.read_text()))
                state.delivered_vars[entry.target] = sorted(delivered)

        state.files[entry.target] = record

    # The live .env, if the site has one, is tracked for append-only variable
    # delivery -- never baselined, never rewritten. Its delivered set starts
    # at whatever it and the canonical example define today.
    live_env = site_dir / ".env"
    if live_env.is_file():
        example = render(_entry_for(".env.example"), params, root).decode("utf-8")
        delivered = set(env_var_names(example)) | set(env_var_names(live_env.read_text()))
        state.delivered_vars[".env"] = sorted(delivered)
        actions.append(Action(".env", "tracked", detail="append-only variable delivery"))

    return actions, state


def _entry_for(target: str) -> SubstrateFile:
    for entry in MANIFEST:
        if entry.target == target:
            return entry
    raise KeyError(target)


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def plan_upgrade(
    site_dir: Path,
    state: State,
    root: Traversable | Path,
    take_upstream: set[str] | None = None,
    keep: set[str] | None = None,
) -> tuple[list[Action], State, int]:
    """Three-way sync per managed file; append-only delivery for config.

    Returns the actions, the updated state, and the number of unresolved
    conflicts. The state's podpack_version advances only when that number is
    zero -- a half-applied upgrade stays visibly pending.
    """
    take_upstream = take_upstream or set()
    keep = keep or set()
    params = Parameters.build(
        state.parameters["site_package"], site_name=state.parameters["site_name"]
    )
    actions: list[Action] = []
    conflicts = 0
    new_version = podpack_version()

    for entry in MANIFEST:
        target = site_dir / entry.target
        record = state.files.get(entry.target)
        rendered = render(entry, params, root)

        if entry.kind in MANAGED_KINDS:
            baseline = record["sha256"] if record else None
            render_hash = sha256(rendered)
            disk = target.read_bytes() if target.exists() else None
            record = {"class": entry.kind, "sha256": render_hash}

            if entry.target in take_upstream:
                actions.append(Action(entry.target, "took upstream", content=rendered,
                                      executable=entry.executable))
                _drop_stale_new(actions, site_dir, entry.target)
            elif entry.target in keep:
                # The site's edit wins; advancing the baseline acknowledges
                # the upstream change without applying it.
                actions.append(Action(entry.target, "kept (upstream acknowledged)"))
            elif disk is None:
                actions.append(Action(entry.target, "restored", content=rendered,
                                      executable=entry.executable))
            elif sha256(disk) == baseline:
                if render_hash == baseline:
                    actions.append(Action(entry.target, "ok"))
                else:
                    actions.append(Action(entry.target, "updated", content=rendered,
                                          executable=entry.executable))
            elif sha256(disk) == render_hash:
                actions.append(Action(entry.target, "already matches new version"))
            elif render_hash == baseline:
                actions.append(Action(entry.target, "locally edited (kept)"))
                record["sha256"] = baseline
            else:
                conflicts += 1
                actions.append(Action(
                    entry.target, "conflict",
                    detail=f"wrote {entry.target}.new -- resolve with "
                           f"--take-upstream or --keep",
                    content=rendered,
                ))
                # Baseline stays put: the conflict is unresolved.
                record["sha256"] = baseline
            state.files[entry.target] = record

        elif entry.kind == CONFIG:
            actions.extend(_plan_var_delivery(site_dir, state, entry.target,
                                              rendered.decode("utf-8"), new_version))

        else:  # SEEDED: never touched again, not even recreated.
            if not target.exists():
                actions.append(Action(entry.target, "removed by site (left alone)"))

    # Live .env: same append-only delivery, from the .env.example canon.
    live_env = site_dir / ".env"
    if live_env.is_file() and ".env" in state.delivered_vars:
        example = render(_entry_for(".env.example"), params, root).decode("utf-8")
        actions.extend(_plan_var_delivery(site_dir, state, ".env", example, new_version))

    # The live secrets.env is never written: an appended default in that file
    # is a weak credential on its way to production. Report instead.
    secrets = site_dir / "secrets.env"
    if secrets.is_file():
        canon = render(_entry_for("secrets.env.example"), params, root).decode("utf-8")
        missing = [name for name in env_var_names(canon)
                   if name not in set(env_var_names(secrets.read_text()))]
        if missing:
            actions.append(Action(
                "secrets.env", "needs new secrets",
                detail="add by hand: " + ", ".join(missing),
            ))

    if conflicts == 0:
        state.podpack_version = new_version
    return actions, state, conflicts


def _plan_var_delivery(
    site_dir: Path, state: State, target_name: str, canon: str, version: str
) -> list[Action]:
    """Append canonical variables this site has never been given.

    Never modifies an existing line; never re-adds a variable the site
    deleted after delivery. Tokens left in a new variable's default (a
    parameter init consumed and did not record) surface as CHANGEME.
    """
    target = site_dir / target_name
    if not target.is_file():
        return [Action(target_name, "removed by site (left alone)")]
    delivered = set(state.delivered_vars.get(target_name, []))
    present = set(env_var_names(target.read_text()))
    blocks = env_var_blocks(canon)
    pending = [n for n in env_var_names(canon) if n not in delivered]
    if not pending:
        return []

    additions = []
    for name in pending:
        if name not in present:
            block = _TOKEN_RE.sub("CHANGEME", blocks[name])
            additions.append(block)
        delivered.add(name)
    state.delivered_vars[target_name] = sorted(delivered)
    if not additions:
        # The site added them itself before we delivered; nothing to write.
        return [Action(target_name, "up to date (site already defines new variables)")]
    text = (
        f"\n# --- added by podpack substrate upgrade (podpack {version}) ---\n"
        + "\n\n".join(additions) + "\n"
    )
    return [Action(target_name, "appended new variables",
                   detail=", ".join(n for n in pending),
                   content=text.encode("utf-8"))]


def _drop_stale_new(actions: list[Action], site_dir: Path, target: str) -> None:
    if (site_dir / f"{target}.new").exists():
        actions.append(Action(f"{target}.new", "removed (conflict resolved)"))


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def apply(actions: list[Action], site_dir: Path) -> None:
    """Execute a plan. Writing is the only side effect; verbs say the rest."""
    for action in actions:
        target = site_dir / action.target
        if action.verb in ("write", "updated", "restored", "took upstream"):
            assert action.content is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(action.content)
            if action.executable:
                target.chmod(target.stat().st_mode | 0o755)
        elif action.verb == "conflict":
            assert action.content is not None
            new_path = site_dir / f"{action.target}.new"
            new_path.write_bytes(action.content)
        elif action.verb == "appended new variables":
            assert action.content is not None
            with target.open("ab") as stream:
                stream.write(action.content)
        elif action.verb == "removed (conflict resolved)":
            (site_dir / action.target).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status(site_dir: Path, state: State, root: Traversable | Path) -> tuple[list[str], bool]:
    """One line per file, and whether an upgrade would act or conflict."""
    params = Parameters.build(
        state.parameters["site_package"], site_name=state.parameters["site_name"]
    )
    lines: list[str] = []
    pending = False

    for entry in MANIFEST:
        target = site_dir / entry.target
        record = state.files.get(entry.target, {})
        baseline = record.get("sha256")
        rendered = render(entry, params, root)
        render_hash = sha256(rendered)
        disk_hash = sha256(target.read_bytes()) if target.exists() else None

        if entry.kind in MANAGED_KINDS:
            if disk_hash is None:
                word, pending = "missing", True
            elif disk_hash == baseline:
                if render_hash == baseline:
                    word = "ok"
                else:
                    word, pending = "update available", True
            elif disk_hash == render_hash:
                word, pending = "update available (already applied)", True
            elif render_hash == baseline:
                word = "locally edited"
            else:
                word, pending = "conflict", True
        elif entry.kind == CONFIG:
            undelivered = [
                n for n in env_var_names(rendered.decode("utf-8"))
                if n not in set(state.delivered_vars.get(entry.target, []))
            ]
            if disk_hash is None:
                word = "removed by site"
            elif undelivered:
                word, pending = f"new variables pending: {', '.join(undelivered)}", True
            elif disk_hash == baseline:
                word = f"seeded at {record.get('seeded_by', '?')}"
            else:
                word = f"seeded at {record.get('seeded_by', '?')}, since edited"
        else:
            if disk_hash is None:
                word = "removed by site"
            elif disk_hash == baseline:
                word = f"seeded at {record.get('seeded_by', '?')}"
            else:
                word = f"seeded at {record.get('seeded_by', '?')}, since edited"
        lines.append(f"{entry.target:32} {word}")

    return lines, pending
