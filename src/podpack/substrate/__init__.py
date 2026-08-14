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
import os
import re
from dataclasses import dataclass, field
from importlib.metadata import version as _distribution_version
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from podpack import services

STATE_FILE = "substrate.json"
STATE_SCHEMA = 1

# File classes. Managed files are podpack's code and upgrade; configuration
# is the site's once delivered and only ever grows; seeded files are written
# once and become the site's outright.
VERBATIM = "verbatim"    # managed, byte-identical
RENDERED = "rendered"    # managed, site parameters substituted
CONFIG = "config"        # seeded, then append-only parameter delivery
SEEDED = "seeded"        # written once if absent, never touched again
FRAGMENT = "fragment"    # joins a CONFIG file's canon when its service is on

# A FRAGMENT has no target of its own: its `target` names the CONFIG file it
# extends. That is what keeps a postgres-only site from ever being told to
# add six MONGODB_ secrets -- and what makes a site that later enables
# mongodb receive exactly those, by the ordinary append rule.

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


BASE_MANIFEST: tuple[SubstrateFile, ...] = (
    SubstrateFile("Containerfile", "Containerfile", RENDERED),
    SubstrateFile("compose.yaml", "compose.yaml", VERBATIM),
    SubstrateFile("dockerignore", ".dockerignore", VERBATIM),
    SubstrateFile("container/healthcheck.py", "container/healthcheck.py", VERBATIM),
    SubstrateFile("scripts/up.sh", "scripts/up.sh", VERBATIM, executable=True),
    # The container-free path: the same site, the same config and the same
    # migration history, run without podman. RENDERED because it names the
    # site's package for `flask --app`.
    SubstrateFile("scripts/dev.sh", "scripts/dev.sh", RENDERED, executable=True),
    SubstrateFile("dev.env.example", "dev.env.example", CONFIG),
    SubstrateFile(
        "scripts/prepare-host-dirs.sh",
        "scripts/prepare-host-dirs.sh",
        VERBATIM,
        executable=True,
    ),
    # The alembic environment is base, not postgres's: `db` and its one
    # history are core to podpack, and ADR-0015 wants moving to a managed
    # PostgreSQL to be a change to one variable and nothing else.
    SubstrateFile("alembic/env.py", "alembic/env.py", VERBATIM),
    SubstrateFile("alembic/script.py.mako", "alembic/script.py.mako", VERBATIM),
    SubstrateFile("alembic.ini", "alembic.ini", VERBATIM),
    SubstrateFile("env.example", ".env.example", CONFIG),
    SubstrateFile("secrets.env.example", "secrets.env.example", CONFIG),
    SubstrateFile("gitignore", ".gitignore", SEEDED),
    SubstrateFile("README.stub.md", "README.md", SEEDED),
    SubstrateFile("alembic/versions/gitkeep", "alembic/versions/.gitkeep", SEEDED),
)

SERVICE_FILES: dict[str, tuple[SubstrateFile, ...]] = {
    # The files each catalogued service brings. Kept here rather than on the
    # descriptor so that `podpack.services` needs to know nothing about the
    # substrate -- the import runs one way. A test pins these keys against
    # the catalogue, so a service cannot be added without them.
    #
    # Note that postgres's targets are exactly where they have always been.
    # Only the manifest *row* moves, and records are keyed by target, so no
    # site sees a file appear or disappear.
    "postgres": (
        SubstrateFile("services/postgres/compose.yaml", "compose.postgres.yaml", VERBATIM),
        SubstrateFile("config/postgresql.conf", "config/postgresql.conf", VERBATIM),
        SubstrateFile("config/pg_hba.conf", "config/pg_hba.conf", VERBATIM),
        SubstrateFile("config/pg_ident.conf", "config/pg_ident.conf", VERBATIM),
        SubstrateFile(
            "db-init/01-create-app-user.sh",
            "db-init/01-create-app-user.sh",
            VERBATIM,
            executable=True,
        ),
        SubstrateFile("services/postgres/env.fragment", ".env.example", FRAGMENT),
        SubstrateFile("services/postgres/secrets.fragment", "secrets.env.example", FRAGMENT),
    ),
    "mongodb": (
        SubstrateFile("services/mongodb/compose.yaml", "compose.mongodb.yaml", VERBATIM),
        SubstrateFile("services/mongodb/mongod.conf", "config/mongod.conf", VERBATIM),
        SubstrateFile(
            "services/mongodb/01-create-app-user.sh",
            "mongodb-init/01-create-app-user.sh",
            VERBATIM,
            executable=True,
        ),
        SubstrateFile("services/mongodb/env.fragment", ".env.example", FRAGMENT),
        SubstrateFile("services/mongodb/secrets.fragment", "secrets.env.example", FRAGMENT),
    ),
}


def _service_files() -> tuple[SubstrateFile, ...]:
    """Every service's real files, in catalogue order.

    Fragments are excluded: they have no target of their own, extending a
    CONFIG file's canon instead.
    """
    return tuple(
        entry
        for name in services.CATALOGUE
        for entry in SERVICE_FILES.get(name, ())
        if entry.kind != FRAGMENT
    )


MANIFEST: tuple[SubstrateFile, ...] = BASE_MANIFEST + _service_files()
"""Every managed file, whatever this site runs.

A service's files are installed even when the site does not enable it --
they are inert text, compose never opens an overlay COMPOSE_FILE does not
name -- because the alternative costs more than the kilobytes. This way
`MANIFEST` stays a constant that needs no site state, `status` stays total,
and the byte-identical dogfood pin covers every service's files for free.
"""

MANAGED_KINDS = (VERBATIM, RENDERED)


@dataclass(frozen=True)
class Parameters:
    """The site facts substituted into rendered files and seeds.

    Everything except the password is recorded in the state file, so that a
    later upgrade delivering a new variable whose default embeds one of these
    substitutes *this site's* value rather than re-deriving a lab default.
    They are all already visible in the committed `.example` files, so
    recording them exposes nothing new.

    The password deliberately is not, and that is what makes the CHANGEME
    sentinel below reachable: an unrecorded parameter leaves its token in
    place, and delivery marks it for the reader instead of inventing a
    credential.
    """

    site_package: str
    site_name: str
    web_port: int | None = None
    db_port: int | None = None
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    site_services: tuple[str, ...] = services.DEFAULT_SERVICES

    RECORDED = ("site_package", "site_name", "web_port", "db_port", "db_name",
                "db_user", "site_services")

    DEFAULT_WEB_PORT = 8458
    DEFAULT_DB_PORT = 5433

    @staticmethod
    def build(site_package: str, **overrides: Any) -> "Parameters":
        """Fill the derivable defaults from the one required fact."""
        dashed = site_package.replace("_", "-")
        values: dict[str, Any] = {
            "site_name": dashed,
            "web_port": Parameters.DEFAULT_WEB_PORT,
            "db_port": Parameters.DEFAULT_DB_PORT,
            "db_name": site_package,
            "db_user": f"{site_package}_app",
            "db_password": f"{dashed}-app-password",  # a lab value, like the seeds'
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        values["site_services"] = services.normalise(values.get("site_services", ()))
        return Parameters(site_package=site_package, **values)

    @staticmethod
    def from_state(recorded: dict[str, str]) -> "Parameters":
        """Rebuild from substrate.json, leaving what was not recorded unset.

        Deliberately *not* `build()`: that fills defaults, which is right when
        a site is being created and wrong when its state file is being read.
        A state written before a parameter was recorded does not tell us the
        site chose the default -- it tells us nothing -- and inventing 8458 or
        `<pkg>_app` there is how a later delivery writes a plausible wrong
        value into a live file. Unset instead, so the token survives and
        delivery marks it CHANGEME.
        """
        values: dict[str, Any] = {
            key: recorded[key] for key in Parameters.RECORDED if key in recorded
        }
        site_package = values.pop("site_package")
        for key in ("web_port", "db_port"):
            if key in values:
                values[key] = int(values[key])
        # The one deliberate exception to "unrecorded stays unset". Absence
        # tells us nothing about a port or a password, so those keep their
        # tokens and delivery marks them CHANGEME. Absence of `site_services`
        # tells us something exact: this state was written before services
        # were a choice, which describes a site whose compose.yaml had
        # postgres welded in. Delivering COMPOSE_FILE=CHANGEME to such a site
        # is the failure this line exists to prevent.
        # normalise, so a state file that predates a service becoming
        # mandatory still describes a site that runs it.
        values["site_services"] = services.normalise(
            [part for part in values.get("site_services", "").split(",") if part]
        )
        return Parameters(site_package=site_package, site_name=values.pop("site_name", ""),
                          **values)

    def recorded(self) -> dict[str, str]:
        values = {key: str(getattr(self, key)) for key in self.RECORDED}
        values["site_services"] = ",".join(self.site_services)
        return values

    def tokens(self) -> dict[str, str]:
        """Substitutions for this site, omitting anything unresolved.

        An empty value means "not known here" rather than "the empty string":
        leaving the token in place is what lets `_plan_var_delivery` mark it
        CHANGEME instead of silently writing a wrong -- or blank -- default.
        """
        candidates = {
            "@@SITE_PACKAGE@@": self.site_package,
            "@@SITE_NAME@@": self.site_name,
            "@@WEB_HOST_PORT@@": str(self.web_port) if self.web_port else "",
            "@@POSTGRES_HOST_PORT@@": str(self.db_port) if self.db_port else "",
            "@@DB_NAME@@": self.db_name,
            "@@DB_USER@@": self.db_user,
            "@@DB_PASSWORD@@": self.db_password,
            "@@COMPOSE_FILE@@": services.compose_file_line(self.site_services),
        }
        # Every service's forwarder default, derived rather than listed, so a
        # new catalogue entry needs no change here. postgres's is the one the
        # site may have chosen at init, and it keeps its own recorded value.
        for name, service in services.CATALOGUE.items():
            candidates.setdefault(
                f"@@{name.upper()}_HOST_PORT@@", str(service.default_host_port)
            )
        return {token: value for token, value in candidates.items() if value}


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
        # A newer podpack may record things this one would misread -- a site
        # pin rolled back, most plausibly. Refusing beats interpreting a
        # schema-2 file under schema-1 assumptions and writing the result.
        schema = raw.get("substrate_schema", 0)
        if schema > STATE_SCHEMA:
            raise RuntimeError(
                f"{path} was written with substrate schema {schema}, and this "
                f"podpack understands {STATE_SCHEMA}. Upgrade podpack, or "
                f"restore the {STATE_FILE} that matches the pinned version."
            )
        missing = {"podpack_version", "parameters", "files"} - set(raw)
        if missing:
            raise RuntimeError(
                f"{path} is missing {', '.join(sorted(missing))}. It is written "
                "by `podpack substrate` and not meant to be edited by hand; "
                "restore it from version control."
            )
        return State(
            podpack_version=raw["podpack_version"],
            parameters=raw["parameters"],
            files=raw["files"],
            # Absent means "nothing has been delivered", which is only true of
            # a file podpack has never seen; every state it writes carries the
            # key, so a missing one is an edited file and the checks above have
            # already had their say.
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


def escapes(site_dir: Path, target: Path) -> str | None:
    """Where `target` really lands, if that is outside the site.

    Testing the leaf for a symlink is not enough and was the first attempt: a
    site that points `config/` or `scripts/` at a shared checkout has an
    ordinary path with a symlinked *parent*, and writing followed it straight
    out of the site. Resolving the whole path is the only check that holds,
    and it covers the dangling link, the link-to-a-directory and the
    hand-planted `.new` symlink in the same breath.
    """
    root = site_dir.resolve()
    try:
        resolved = target.resolve()
    except OSError:
        return str(target)
    if resolved == root or root in resolved.parents:
        return None
    return str(resolved)


def unusable(target: Path) -> str | None:
    """Why this path cannot be treated as the file it is supposed to be."""
    if target.exists() and not target.is_file():
        return "not a regular file"
    if target.is_symlink() and not target.exists():
        return "a dangling symlink"
    return None


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


def config_canon(target: str, params: Parameters, root: Traversable | Path) -> bytes:
    """A CONFIG file's canonical content for *this* site.

    The base file, plus the fragment of every service the site declares. It
    is what makes per-service variables and secrets work in both directions:
    a postgres-only site is never told to add MONGODB_ secrets, and a site
    that later declares mongodb receives exactly those by the ordinary
    append rule, with no special case anywhere in the delivery code.
    """
    text = render(_entry_for(target), params, root)
    for name in services.CATALOGUE:
        if name not in params.site_services:
            continue
        for entry in SERVICE_FILES.get(name, ()):
            if entry.kind == FRAGMENT and entry.target == target:
                text += b"\n" + render(entry, params, root)
    return text


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
    state = State(podpack_version=podpack_version(), parameters=params.recorded())

    for entry in MANIFEST:
        target = site_dir / entry.target
        # A CONFIG file is seeded as the *canon*: the base plus the fragment
        # of every service this site declared. Seeding the base alone would
        # write a secrets.env.example with no database block in it.
        rendered = (
            config_canon(entry.target, params, root)
            if entry.kind == CONFIG
            else render(entry, params, root)
        )
        record: dict[str, Any] = {"class": entry.kind, "sha256": sha256(rendered)}

        # Before anything reads or writes: a path that resolves outside the
        # site is not this site's to manage, and init is the first command a
        # new site runs -- the worst possible moment to follow a link out.
        outside = escapes(site_dir, target)
        broken = unusable(target)
        if outside or broken:
            actions.append(Action(
                entry.target, "not managed here",
                detail=f"resolves outside the site ({outside})" if outside
                       else f"is {broken}",
            ))
            record["unmanaged"] = True
            state.files[entry.target] = record
            continue

        if entry.kind in MANAGED_KINDS:
            if not target.exists():
                actions.append(Action(entry.target, "write", content=rendered,
                                      executable=entry.executable))
            elif target.read_bytes() == rendered:
                actions.append(Action(entry.target, "adopted"))
                if entry.executable and not os.access(target, os.X_OK):
                    # Byte-identical but not runnable: a copy that travelled
                    # through something which drops the mode bit would be
                    # baselined `ok` for ever while podman fails to run it.
                    actions.append(Action(entry.target, "made executable",
                                          executable=True))
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
                # The canon, not the base file: a service's fragment is part
                # of what this site has been offered, or its variables would
                # be delivered all over again on the next upgrade.
                canon_names = env_var_names(
                    config_canon(entry.target, params, root).decode("utf-8")
                )
                delivered = set(canon_names)
                if target.exists():
                    # Anything the site already defines counts as delivered:
                    # upgrade must never push a variable back at a site that
                    # has it, or had it and removed it. Adoption cannot tell
                    # "deleted deliberately" from "never had it", and treats
                    # both as the site's business -- so a canonical variable
                    # its copy lacks is recorded as delivered and never
                    # appended. Reported rather than silent, because the
                    # difference is only visible now.
                    site_names = set(env_var_names(target.read_text()))
                    delivered |= site_names
                    absent = [n for n in canon_names if n not in site_names]
                    if absent:
                        actions.append(Action(
                            entry.target, "not adding what it lacks",
                            detail="podpack's copy also defines "
                                   + ", ".join(absent),
                        ))
                state.delivered_vars[entry.target] = sorted(delivered)

        state.files[entry.target] = record

    # The live .env, if the site has one, is tracked for append-only variable
    # delivery -- never baselined, never rewritten. Its delivered set starts
    # at whatever it and the canonical example define today.
    live_env = site_dir / ".env"
    if live_env.is_file():
        example = config_canon(".env.example", params, root).decode("utf-8")
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
    params = Parameters.from_state(state.parameters)
    # Captured before the delivery pass below extends it: an untracked live
    # .env is seeded from what this site has *already* been offered, and the
    # variables this very upgrade introduces must not count as offered.
    example_delivered = set(state.delivered_vars.get(".env.example", []))
    actions: list[Action] = []
    conflicts = 0
    new_version = podpack_version()

    for entry in MANIFEST:
        target = site_dir / entry.target
        record = state.files.get(entry.target)
        rendered = render(entry, params, root)

        if entry.kind in MANAGED_KINDS:
            baseline = record.get("sha256") if record else None
            render_hash = sha256(rendered)
            record = {"class": entry.kind, "sha256": render_hash}

            # Checked before anything reads the path: a site that points this
            # file -- or the directory holding it -- somewhere of its own has
            # taken it out of podpack's hands, and following the link would
            # write outside the site, which walking a manifest is supposed to
            # make impossible. Recorded as unmanaged so that saying so once is
            # not the same as reporting work that will never happen.
            outside = escapes(site_dir, target)
            broken = unusable(target)
            if outside or broken:
                actions.append(Action(
                    entry.target, "not managed here",
                    detail=f"resolves outside the site ({outside})" if outside
                           else f"is {broken}",
                ))
                record["sha256"] = baseline if baseline else render_hash
                record["unmanaged"] = True
                state.files[entry.target] = record
                continue

            disk = target.read_bytes() if target.exists() else None

            # The three-way decision comes first, so that a resolution flag
            # can only ever resolve something. Naming a file that is *not* in
            # conflict used to act anyway: --keep recorded the new baseline
            # without writing, swallowing the upstream change for ever, and
            # --take-upstream overwrote a site edit that nothing had asked
            # about.
            if disk is None:
                decision = "restored"
            elif sha256(disk) == baseline:
                decision = "ok" if render_hash == baseline else "updated"
            elif sha256(disk) == render_hash:
                decision = "already matches new version"
            elif render_hash == baseline:
                decision = "locally edited (kept)"
            else:
                decision = "conflict"

            # --take-upstream answers "discard what this site has here", so it
            # applies wherever the site's copy differs -- a conflict, or an
            # edit made before adoption. --keep answers "my edit wins over
            # this incoming change", which only a conflict poses; on an
            # unmodified file it would freeze the copy at the old version
            # while recording the new baseline, silently losing the update.
            takeable = decision in ("conflict", "locally edited (kept)")
            named_take = entry.target in take_upstream
            named_keep = entry.target in keep
            if (named_take and not takeable) or (named_keep and decision != "conflict"):
                actions.append(Action(
                    entry.target, "flag ignored",
                    detail=f"nothing to resolve ({decision})",
                ))
            elif named_take:
                # The site's version is kept beside the file rather than
                # simply lost: `--take-upstream` is an explicit request, but
                # "nothing is ever clobbered" is a promise the whole design
                # rests on, and a discarded edit is exactly the thing nobody
                # can reconstruct.
                if disk is not None and disk != rendered:
                    actions.append(Action(f"{entry.target}.orig", "saved your version",
                                          content=disk))
                actions.append(Action(entry.target, "took upstream", content=rendered,
                                      executable=entry.executable))
                _drop_stale_new(actions, site_dir, entry.target, rendered)
                decision = "resolved"
            elif decision == "conflict" and entry.target in keep:
                # The site's edit wins; advancing the baseline acknowledges
                # the upstream change without applying it. The .new copy goes
                # either way -- a resolved conflict should leave no artifact,
                # and one left behind would quietly go stale.
                actions.append(Action(entry.target, "kept (upstream acknowledged)"))
                _drop_stale_new(actions, site_dir, entry.target, rendered)
                decision = "resolved"

            if decision in ("restored", "updated"):
                actions.append(Action(entry.target, decision, content=rendered,
                                      executable=entry.executable))
            elif decision in ("ok", "already matches new version"):
                actions.append(Action(entry.target, decision))
                # A conflict the site resolved by hand -- copying the .new
                # over, the obvious move -- leaves the artifact behind to go
                # stale and be committed. Clear it once the file agrees.
                _drop_stale_new(actions, site_dir, entry.target, rendered)
                if entry.executable and not os.access(target, os.X_OK):
                    # A hand-copied script that arrived without its exec bit
                    # is byte-identical and would otherwise be baselined `ok`
                    # for ever, while podman fails to run it.
                    actions.append(Action(entry.target, "made executable",
                                          executable=True))
            elif decision == "locally edited (kept)":
                actions.append(Action(entry.target, decision))
                record["sha256"] = baseline
            elif decision == "conflict":
                conflicts += 1
                actions.append(Action(
                    entry.target, "conflict",
                    detail=f"podpack's version goes to {entry.target}.new -- "
                           f"resolve with --take-upstream or --keep",
                    content=rendered,
                ))
                # Baseline stays put: the conflict is unresolved.
                record["sha256"] = baseline
            state.files[entry.target] = record

        elif record is None:
            # A CONFIG or SEEDED file this podpack has added since the site
            # was built. "Seeded once" means once per site, not once ever:
            # with no record, podpack has never delivered this file here, so
            # its absence is not the site having removed it. Without this a
            # new seed reaches new sites only, which is the gap the seeded
            # class is already awkward about -- and it would have arrived
            # silently, since nothing reports a file that was never offered.
            content = (
                config_canon(entry.target, params, root)
                if entry.kind == CONFIG else rendered
            )
            actions.append(Action(entry.target, "write", content=content,
                                  executable=entry.executable))
            state.files[entry.target] = {
                "class": entry.kind, "sha256": sha256(content),
                "seeded_by": new_version,
            }
            if entry.kind == CONFIG:
                state.delivered_vars[entry.target] = sorted(
                    env_var_names(content.decode("utf-8"))
                )

        elif entry.kind == CONFIG:
            actions.extend(_plan_var_delivery(
                site_dir, state, entry.target,
                config_canon(entry.target, params, root).decode("utf-8"),
                new_version))

        else:  # SEEDED: never touched again, not even recreated.
            if not target.exists():
                actions.append(Action(entry.target, "removed by site (left alone)"))

    # Live .env: same append-only delivery, from the .env.example canon.
    #
    # Tracked on first sighting rather than only when init saw one, because the
    # documented order creates it *after* init -- init is what writes the
    # example it is copied from. Requiring it at init meant a site following the
    # guide never received a new variable, silently and for ever.
    live_env = site_dir / ".env"
    if live_env.is_file():
        example = config_canon(".env.example", params, root).decode("utf-8")
        if ".env" not in state.delivered_vars:
            # What the file already defines, plus what its example had been
            # offered before today: adopting must neither dump the whole
            # example in nor swallow the variables this upgrade introduces.
            state.delivered_vars[".env"] = sorted(
                example_delivered | set(env_var_names(live_env.read_text()))
            )
            actions.append(Action(".env", "tracked", detail="append-only variable delivery"))
        actions.extend(_plan_var_delivery(site_dir, state, ".env", example, new_version))

    # The live secrets.env is never written: an appended default in that file
    # is a weak credential on its way to production. Report instead.
    secrets = site_dir / "secrets.env"
    if secrets.is_file():
        canon = config_canon("secrets.env.example", params, root).decode("utf-8")
        missing = _missing_secrets(secrets.read_text(), canon)
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
    outside = escapes(site_dir, target)
    if outside:
        # Appending follows a link exactly as writing does, and `is_file()`
        # says nothing about where the file is.
        return [Action(target_name, "not managed here",
                       detail=f"resolves outside the site ({outside})")]
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
    # Re-baseline to what the file becomes, so podpack's own append is not
    # reported back to the site as "since edited" -- the one edit it can be
    # sure the site did not make. Only where the file was otherwise unedited,
    # though: rolling the baseline forward on a file the site had changed
    # would clear its drift report as a side effect of an unrelated delivery.
    record = state.files.get(target_name)
    current = target.read_bytes()
    if record is not None and record.get("sha256") == sha256(current):
        record["sha256"] = sha256(current + text.encode("utf-8"))
    return [Action(target_name, "appended new variables",
                   detail=", ".join(n for n in pending),
                   content=text.encode("utf-8"))]


def _missing_secrets(live: str, canon: str) -> list[str]:
    """Canonical secrets this site's file does not account for.

    A commented-out definition counts as accounted for: a site that manages a
    secret elsewhere -- a vault, the platform's own store -- and left
    `# API_TOKEN=` behind as a note has answered the question, and reporting
    it on every upgrade for ever would be the kind of noise that teaches
    people to skip the report entirely.
    """
    known = set(env_var_names(live))
    for line in live.splitlines():
        stripped = line.lstrip().lstrip("#").lstrip()
        match = _VAR_LINE_RE.match(stripped)
        if line.lstrip().startswith("#") and match:
            known.add(match.group(1))
    return [name for name in env_var_names(canon) if name not in known]


def _drop_stale_new(
    actions: list[Action], site_dir: Path, target: str, rendered: bytes
) -> None:
    """Clear the conflict artifact, unless the site has made it its own.

    The conflict message invites reading `<file>.new`, and some people merge
    into it. Deleting only what podpack itself wrote keeps that work safe --
    and a copy that no longer matches podpack's version is, by definition,
    somebody's edit.
    """
    path = site_dir / f"{target}.new"
    if not path.is_file():
        return
    if path.read_bytes() == rendered:
        actions.append(Action(f"{target}.new", "removed (conflict resolved)"))
    else:
        actions.append(Action(
            f"{target}.new", "left alone",
            detail="you have edited it; delete it when you are done",
        ))


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


WRITE_VERBS = ("write", "updated", "restored", "took upstream", "saved your version")


def apply(actions: list[Action], site_dir: Path) -> None:
    """Execute a plan. Writing is the only side effect; verbs say the rest.

    Every mutation re-checks containment. The plans already refuse a path
    that resolves outside the site, so this is belt and braces -- but it is
    the only place bytes actually move, and a guard on the wrong side of
    `parent.mkdir` is how the first attempt let a symlinked directory
    through.
    """
    for action in actions:
        target = site_dir / action.target
        mutates = action.verb in WRITE_VERBS or action.verb in (
            "made executable", "conflict", "appended new variables",
            "removed (conflict resolved)",
        )
        if mutates and escapes(site_dir, target if action.verb != "conflict"
                               else site_dir / f"{action.target}.new"):
            continue

        if action.verb in WRITE_VERBS:
            assert action.content is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            if escapes(site_dir, target):   # the mkdir may have made it so
                continue
            target.write_bytes(action.content)
            if action.executable:
                target.chmod(target.stat().st_mode | 0o755)
        elif action.verb == "made executable":
            target.chmod(target.stat().st_mode | 0o755)
        elif action.verb == "conflict":
            assert action.content is not None
            new_path = site_dir / f"{action.target}.new"
            # Idempotent: a re-run of the same unresolved conflict rewrites
            # nothing, so a copy the site has been reading (or hand-merging)
            # keeps its timestamp.
            if not new_path.exists() or new_path.read_bytes() != action.content:
                new_path.write_bytes(action.content)
        elif action.verb == "appended new variables":
            assert action.content is not None
            with target.open("ab") as stream:
                stream.write(action.content)
        elif action.verb == "removed (conflict resolved)":
            target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status(site_dir: Path, state: State, root: Traversable | Path) -> tuple[list[str], bool]:
    """One line per file, and whether an upgrade would act or conflict."""
    params = Parameters.from_state(state.parameters)
    lines: list[str] = []
    pending = False

    for entry in MANIFEST:
        target = site_dir / entry.target
        record = state.files.get(entry.target, {})
        baseline = record.get("sha256")
        rendered = render(entry, params, root)
        render_hash = sha256(rendered)

        # Reported as a settled fact, not as pending work: a file the site has
        # pointed elsewhere is one podpack cannot manage, and a check that can
        # never go green is a check nobody keeps.
        outside = escapes(site_dir, target)
        broken = unusable(target)
        if outside or broken:
            word = f"not managed here ({'outside the site' if outside else broken})"
            lines.append(f"{entry.target:32} {word}")
            continue

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
                n for n in env_var_names(
                    config_canon(entry.target, params, root).decode("utf-8")
                )
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

    # The live .env is not a manifest file -- podpack never writes it wholesale
    # -- but it does receive new variables, so leaving it out of the report
    # would hide the one thing an upgrade will do to it.
    live_env = site_dir / ".env"
    if live_env.is_file() and not escapes(site_dir, live_env):
        example = config_canon(".env.example", params, root).decode("utf-8")
        # The same seeding upgrade applies on first sighting, or status would
        # report every variable the site trimmed from .env as pending work
        # that an upgrade then declines to do.
        delivered = set(state.delivered_vars.get(".env")
                        or state.delivered_vars.get(".env.example", []))
        undelivered = [
            name for name in env_var_names(example)
            if name not in delivered
            and name not in set(env_var_names(live_env.read_text()))
        ]
        if undelivered:
            pending = True
            lines.append(f"{'.env':32} new variables pending: {', '.join(undelivered)}")
        else:
            lines.append(f"{'.env':32} ok (append-only)")

    # Likewise secrets.env, which upgrade reports on but never writes. Leaving
    # it out made `status --check` say "nothing to do" while an upgrade would
    # have told the site it was missing a required secret.
    secrets = site_dir / "secrets.env"
    if secrets.is_file():
        canon = config_canon("secrets.env.example", params, root).decode("utf-8")
        missing = _missing_secrets(secrets.read_text(), canon)
        if missing:
            pending = True
            lines.append(f"{'secrets.env':32} add by hand: {', '.join(missing)}")
        else:
            lines.append(f"{'secrets.env':32} ok (never written by podpack)")

    return lines, pending
