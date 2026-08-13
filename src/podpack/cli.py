"""The podpack command line.

One console script with room for future subcommands; today it carries
`substrate`, the install-and-upgrade command for the container substrate
(ADR-0026). argparse rather than a CLI framework: four subcommands and a
dozen flags is inside stdlib territory, and podpack takes no dependency it
can do without.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import tomllib
from pathlib import Path

from . import substrate
from .substrate import Action, Parameters, State


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = {
        "init": _init,
        "upgrade": _upgrade,
        "status": _status,
        "services": _services,
        "diff": _diff,
    }[args.subcommand]
    return handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="podpack",
        description="The podpack framework's command line.",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"podpack {substrate.podpack_version()}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser(
        "substrate",
        help="install and upgrade a site's copy of the container substrate",
        description=(
            "The substrate ships inside the podpack package; these commands "
            "keep a site's copy current. Managed files sync three-way and "
            "never clobber a site edit; configuration files only ever gain "
            "new parameters; config/app.toml, alembic/versions/ and the "
            "site's own scripts are never touched. State lives in "
            f"{substrate.STATE_FILE}, which the site commits."
        ),
    )
    actions = sub.add_subparsers(dest="subcommand", required=True)

    init = actions.add_parser("init", help="lay down the substrate, or adopt an existing copy")
    init.add_argument("--dir", default=".", help="site directory (default: .)")
    init.add_argument("--site-package", help="the site's import name; default: derived from pyproject.toml")
    init.add_argument("--site-name", help="compose project/image name; default: site package, dashed")
    init.add_argument("--web-port", type=int, default=None, help="host port for the web service (default 8458)")
    init.add_argument("--db-port", type=int, default=None, help="host port for PostgreSQL (default 5433)")
    init.add_argument("--db-name", default=None, help="database name (default: site package)")
    init.add_argument("--db-user", default=None, help="application role (default: <site package>_app)")
    init.add_argument("--db-password", default=None, help="lab password for the application role")
    init.add_argument("--services", default=None,
                      help="comma-separated backing services this site runs "
                           f"(default: {','.join(substrate.services.DEFAULT_SERVICES)}; "
                           f"available: {','.join(substrate.services.names())})")
    init.add_argument("--dry-run", action="store_true", help="report without writing")
    init.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    upgrade = actions.add_parser("upgrade", help="bring the substrate up to the installed podpack")
    upgrade.add_argument("--dir", default=".")
    upgrade.add_argument("--dry-run", action="store_true", help="report without writing")
    upgrade.add_argument("--take-upstream", action="append", default=[], metavar="PATH",
                         help="take podpack's version of PATH, whether it conflicts "
                              "or you had simply edited it; your version is kept "
                              "beside it as PATH.orig")
    upgrade.add_argument("--keep", action="append", default=[], metavar="PATH",
                         help="resolve a conflict by keeping the site's PATH as-is")

    stat = actions.add_parser("status", help="report every substrate file's state")
    stat.add_argument("--dir", default=".")
    stat.add_argument("--check", action="store_true",
                      help="exit 1 if an upgrade would write or conflict")

    svc = actions.add_parser(
        "services",
        help="report the site's backing services, or add one",
        description=(
            "Which backing services this site runs is the site owner's "
            "decision, taken independently of what any installed app might "
            "like. Services are addable; there is deliberately no --remove, "
            "because taking a store away is a decision about data rather "
            "than about configuration -- edit COMPOSE_FILE yourself, and "
            "nothing podpack owns will have destroyed anything."
        ),
    )
    svc.add_argument("--dir", default=".")
    svc.add_argument("--add", metavar="NAME", action="append", default=[],
                     help="enable a catalogued service for this site")
    svc.add_argument("--dry-run", action="store_true", help="report without writing")

    diff = actions.add_parser("diff", help="diff substrate files against the installed version")
    diff.add_argument("--dir", default=".")
    diff.add_argument("paths", nargs="*", help="targets to diff (default: all that differ)")

    return parser


def _site_dir(args: argparse.Namespace) -> Path:
    return Path(args.dir).resolve()


def _report(actions: list[Action]) -> None:
    for action in actions:
        line = f"  {action.verb:>34}  {action.target}"
        if action.detail:
            line += f"  ({action.detail})"
        print(line)


def _derive_site_package(site_dir: Path) -> str | None:
    """The site's import name, the way uv_build derives it.

    Normalisation is PEP 503's, then dashes to underscores: runs of `-`, `_`
    and `.` collapse to one underscore, and the result is lower-cased --
    verified against uv_build, which builds `src/my_site/` for a project
    named `My-Site`. Deriving `My_Site` instead would put a module name in
    the Containerfile that gunicorn cannot import.
    """
    pyproject = site_dir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    with pyproject.open("rb") as stream:
        data = tomllib.load(stream)
    name = data.get("project", {}).get("name")
    if not isinstance(name, str) or not name:
        return None
    return re.sub(r"[-_.]+", "_", name).lower()


def _init(args: argparse.Namespace) -> int:
    site_dir = _site_dir(args)
    if not site_dir.is_dir():
        # apply() creates missing parents, so a mistyped --dir would otherwise
        # plant a complete substrate somewhere nobody asked for and exit 0.
        print(f"{site_dir} is not a directory -- create the site first")
        return 2
    if State.load(site_dir) is not None:
        print(f"{substrate.STATE_FILE} already exists here -- this site is "
              "initialised; `podpack substrate upgrade` is the command that "
              "moves it forward.")
        return 2

    site_package = args.site_package or _derive_site_package(site_dir)
    if not site_package:
        print("cannot derive the site package (no [project] name in "
              "./pyproject.toml); say it explicitly with --site-package")
        return 2

    declared = (
        tuple(part.strip() for part in args.services.split(",") if part.strip())
        if args.services else None
    )
    if declared:
        unknown_services = substrate.services.unknown(declared)
        if unknown_services:
            print("no such core service: " + ", ".join(unknown_services))
            print("available: " + ", ".join(substrate.services.names()))
            return 2
    params = Parameters.build(
        site_package,
        site_name=args.site_name,
        site_services=declared,
        web_port=args.web_port,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
    )
    print(f"site package: {params.site_package}   site name: {params.site_name}")
    print(f"ports: web {params.web_port}, postgres {params.db_port}   "
          f"database: {params.db_name} as {params.db_user}")
    print(f"services: {', '.join(params.site_services)}   "
          f"COMPOSE_FILE={substrate.services.compose_file_line(params.site_services)}")
    if not args.yes and not args.dry_run and sys.stdin.isatty():
        answer = input("Proceed? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("nothing written")
            return 1

    actions, state = substrate.plan_init(site_dir, params, substrate.source_root())
    _report(actions)
    if args.dry_run:
        print("(dry run: nothing written)")
        return 0
    substrate.apply(actions, site_dir)
    state.save(site_dir)
    print(f"recorded in {substrate.STATE_FILE} (commit it with the rest)")
    return 0


def _load_or_complain(site_dir: Path) -> State | None:
    """The state, or None with the reason printed.

    Every "cannot proceed" here exits 2, which is what separates a damaged
    state file from `upgrade`'s exit 1 for conflicts to resolve -- a CI gate
    keyed on 1 would otherwise read corruption as ordinary pending work.
    """
    try:
        state = State.load(site_dir)
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"cannot read {substrate.STATE_FILE}: {exc}")
        return None
    if state is None:
        print(f"no {substrate.STATE_FILE} here -- run `podpack substrate init` first")
    return state


def _unknown_targets(paths: list[str]) -> list[str]:
    managed = {entry.target for entry in substrate.MANIFEST
               if entry.kind in substrate.MANAGED_KINDS}
    return sorted(set(paths) - managed)


def _upgrade(args: argparse.Namespace) -> int:
    site_dir = _site_dir(args)
    state = _load_or_complain(site_dir)
    if state is None:
        return 2
    # A mistyped path would otherwise match nothing and vanish, leaving the
    # conflict it was meant to resolve reported again with no hint why.
    both = sorted(set(args.take_upstream) & set(args.keep))
    if both:
        # Branch order would decide it silently, and the loser is the site's
        # own version -- so the contradiction is a usage error, not a race.
        print("named in both --take-upstream and --keep: " + ", ".join(both))
        return 2
    unknown = _unknown_targets(args.take_upstream + args.keep)
    if unknown:
        print("not managed substrate files: " + ", ".join(unknown))
        print("resolvable paths are: " + ", ".join(
            entry.target for entry in substrate.MANIFEST
            if entry.kind in substrate.MANAGED_KINDS
        ))
        return 2
    actions, state, conflicts = substrate.plan_upgrade(
        site_dir,
        state,
        substrate.source_root(),
        take_upstream=set(args.take_upstream),
        keep=set(args.keep),
    )
    _report([a for a in actions if a.verb != "ok"])
    if args.dry_run:
        print("(dry run: nothing written)")
        return 1 if conflicts else 0
    substrate.apply(actions, site_dir)
    state.save(site_dir)
    if conflicts:
        print(f"{conflicts} conflict(s): the .new files hold podpack's version; "
              "resolve each with --take-upstream PATH or --keep PATH")
        return 1
    return 0


def _status(args: argparse.Namespace) -> int:
    site_dir = _site_dir(args)
    state = _load_or_complain(site_dir)
    if state is None:
        return 2
    lines, pending = substrate.status(site_dir, state, substrate.source_root())
    installed = substrate.podpack_version()
    print(f"substrate recorded at podpack {state.podpack_version}; "
          f"installed podpack is {installed}")
    for line in lines:
        print(f"  {line}")
    if args.check and pending:
        return 1
    return 0


def _services(args: argparse.Namespace) -> int:
    site_dir = _site_dir(args)
    state = _load_or_complain(site_dir)
    if state is None:
        return 2
    catalogue = substrate.services.CATALOGUE
    enabled = list(Parameters.from_state(state.parameters).site_services)

    if not args.add:
        print(f"COMPOSE_FILE  {substrate.services.compose_file_line(enabled)}")
        for name, service in catalogue.items():
            mark = "enabled  " if name in enabled else "available"
            print(f"  {name:10} {mark}  {service.summary}")
        return 0

    unknown_services = substrate.services.unknown(args.add)
    if unknown_services:
        print("no such core service: " + ", ".join(unknown_services))
        print("available: " + ", ".join(substrate.services.names()))
        return 2

    added = [name for name in args.add if name not in enabled]
    if not added:
        print(f"already enabled: {', '.join(args.add)}")
        return 0
    enabled.extend(added)
    ordered = tuple(name for name in catalogue if name in enabled)
    line = substrate.services.compose_file_line(ordered)

    print(f"adding: {', '.join(added)}")
    print(f"COMPOSE_FILE  {line}")
    if args.dry_run:
        print("(dry run: nothing written)")
        return 0

    # Rewriting a line in .env is something only this command may do, and only
    # because rewriting exactly that line is what it was asked for. The
    # append-only rule protects a site's config from an *upgrade* changing it
    # unasked; an explicit `services --add` is the opposite of unasked.
    state.parameters["site_services"] = ",".join(ordered)
    for name in (".env", ".env.example"):
        target = site_dir / name
        if target.is_file() and not substrate.escapes(site_dir, target):
            text = target.read_text()
            if "COMPOSE_FILE=" in text:
                target.write_text(
                    re.sub(r"^COMPOSE_FILE=.*$", f"COMPOSE_FILE={line}", text, flags=re.M)
                )
                print(f"  set COMPOSE_FILE in {name}")

    # The new service's own variables arrive by the ordinary delivery rule --
    # its fragment is part of this site's canon from now on.
    actions, state, conflicts = substrate.plan_upgrade(site_dir, state, substrate.source_root())
    substrate.apply(actions, site_dir)
    state.save(site_dir)
    _report([a for a in actions if a.verb not in ("ok", "locally edited (kept)")])

    print()
    print("Still yours to do, because podpack writes neither secrets nor host state:")
    for name in added:
        service = catalogue[name]
        print(f"  1. add {service.uri} and its credentials to secrets.env "
              f"(secrets.env.example now shows them)")
    print("  2. ./scripts/prepare-host-dirs.sh   (creates the new host directories)")
    print("  3. podman compose down && ./scripts/up.sh")
    return 1 if conflicts else 0


def _diff(args: argparse.Namespace) -> int:
    site_dir = _site_dir(args)
    state = _load_or_complain(site_dir)
    if state is None:
        return 2
    params = Parameters.from_state(state.parameters)
    unknown = sorted(set(args.paths) - {entry.target for entry in substrate.MANIFEST})
    if unknown:
        # Silence here reads as "no differences", which is the opposite of
        # what a typo should tell you.
        print("not substrate files: " + ", ".join(unknown))
        return 2
    targets = args.paths or [entry.target for entry in substrate.MANIFEST]
    for entry in substrate.MANIFEST:
        if entry.target not in targets:
            continue
        rendered = substrate.render(entry, params, substrate.source_root())
        target = site_dir / entry.target
        disk = target.read_bytes() if target.exists() else b""
        if disk == rendered:
            continue
        diff = difflib.unified_diff(
            rendered.decode("utf-8", errors="replace").splitlines(keepends=True),
            disk.decode("utf-8", errors="replace").splitlines(keepends=True),
            fromfile=f"podpack {substrate.podpack_version()}: {entry.target}",
            tofile=f"site: {entry.target}",
        )
        sys.stdout.writelines(diff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
