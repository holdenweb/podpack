#!/usr/bin/env python3
"""Turn a fresh clone into a configured site: one command, no hand editing.

Everything this does was a manual step, and every one of them has gone wrong
at least once on a real deployment:

  * secrets copied from the examples and left as lab values, so a site ran on
    credentials published in this repository;
  * `POSTGRES_APP_PASSWORD` changed but not the copy inside
    `SQLALCHEMY_DATABASE_URI`, so PostgreSQL came up healthy and `migrate`
    could not authenticate;
  * SELinux relabelling forgotten, so PostgreSQL came up healthy and `migrate`
    could not authenticate -- the same symptom, a different cause, which is
    exactly why guessing is expensive;
  * a generated password containing a character that something downstream
    treats as syntax.

Run it once, after cloning, on the host:

    python3 scripts/configure-host.py --port 8461

Then `./scripts/prepare-host-dirs.sh && ./scripts/up.sh`.

It refuses to overwrite an existing `.env` or `secrets.env`, because those
carry the identity of a running site: the salt that every stored password is
keyed on, and the role the database was bootstrapped with. Regenerating them
is not reconfiguration, it is losing the database.

Standard library only, and no imports from podpack: this runs on the host's
system Python before anything has been built or installed.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

# Names that carry a credential, so that "same as the example" is a finding
# rather than a coincidence. A password left at a lab value fails to
# authenticate; a bind address left at 127.0.0.1 is simply correct.
CREDENTIAL = re.compile(r"(PASSWORD|SECRET|SALT|TOKEN|_KEY|URI)", re.I)


# token_urlsafe yields [A-Za-z0-9_-] and nothing else, which is the point.
#
# Every generated value passes through at least four parsers -- compose's .env
# reader, the shell, a PostgreSQL URI, and Python's configparser inside alembic
# -- and each has characters it treats as syntax: `$` and `#` in an env file,
# quotes and backticks in a shell, `%` in configparser, `@` and `:` in a URI.
# A password is not worth a debugging session, so the alphabet simply excludes
# everything anybody might punctuate with.
def generated(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def selinux_is_enforcing() -> bool:
    """Whether this host will deny the containers their bind mounts.

    Read from /sys, which needs no tools installed and no privileges. A host
    without SELinux has no such file at all.
    """
    try:
        mode = Path("/sys/fs/selinux/enforce").read_text().strip()
    except OSError:
        return False
    return mode == "1"


def check_prerequisites() -> list[str]:
    """Report host problems that would otherwise surface as puzzling failures.

    Reported rather than fatal: this script writes configuration, and a host
    that cannot yet run containers can still be configured. Each of these has
    cost a failed deployment.
    """
    problems = []

    provider = subprocess.run(
        ["podman", "compose", "version"], capture_output=True, text=True
    )
    if provider.returncode != 0:
        problems.append("`podman compose version` failed -- is podman installed?")
    elif "Docker Compose" not in provider.stdout:
        problems.append(
            "the compose provider is not Compose v2 -- podman-compose silently "
            "ignores `depends_on` conditions, which is what sequences migrate "
            "before web. Install the v2 binary and set PODMAN_COMPOSE_PROVIDER."
        )

    # Linux only: on macOS and Windows podman runs in a VM and the client
    # reaches it another way entirely, so looking for this path would report a
    # problem that does not exist on the one platform where none of this
    # matters.
    sock = Path(f"/run/user/{os.getuid()}/podman/podman.sock")
    if sys.platform.startswith("linux") and not sock.exists():
        problems.append(
            f"{sock} is missing -- Compose v2 talks to podman through it. "
            "`systemctl --user enable --now podman.socket`"
        )

    linger = Path(f"/var/lib/systemd/linger/{os.environ.get('USER', '')}")
    if sys.platform.startswith("linux") and Path("/var/lib/systemd/linger").is_dir() and not linger.exists():
        problems.append(
            "lingering is off, so the containers and the podman socket stop "
            f"when you log out. `loginctl enable-linger {os.getuid()}`"
        )
    return problems


def fill(example: Path, target: Path, values: dict[str, str]) -> list[str]:
    """Write `target` from `example`, replacing the values we know.

    Line by line rather than by template, so every comment in the example
    survives -- they carry most of what a reader needs, including which
    settings must never change once a site is live.
    """
    assignment = re.compile(r"^([A-Z][A-Z0-9_]*)=")
    out, seen = [], []
    for line in example.read_text().splitlines(keepends=True):
        match = assignment.match(line)
        if match and match.group(1) in values:
            name = match.group(1)
            out.append(f"{name}={values[name]}\n")
            seen.append(name)
        else:
            out.append(line)
    unplaced = [name for name in values if name not in seen]
    for name in unplaced:                       # a fragment's variable, appended
        out.append(f"{name}={values[name]}\n")
    target.write_text("".join(out))
    target.chmod(0o600)
    return seen + unplaced


def still_at_example_values(example: Path, target: Path, set_here: list[str]) -> list[str]:
    """Variables this script did not know about and therefore did not set.

    A site adds its own -- holdenweb.com needs `MAIL_PASSWORD` -- and those
    keep whatever the example carried, which for a credential is a lab value
    that will not work and may not look wrong. podpack's boot check catches
    `CHANGEME` and unsubstituted tokens but cannot know that
    `lab-only-secret-key` was meant to be replaced, so this says so here, while
    somebody is looking.
    """
    assignment = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")

    def values(path: Path) -> dict[str, str]:
        found = {}
        for line in path.read_text().splitlines():
            match = assignment.match(line.strip())
            if match:
                found[match.group(1)] = match.group(2)
        return found

    sample, written = values(example), values(target)
    return sorted(
        name for name, value in written.items()
        if name not in set_here            # what we set is not "left behind",
        and name in sample                 # even when it equals the example --
        and value == sample[name]          # 127.0.0.1 is both a sane default
        and value.strip()                  # and what the example suggests.
    )


def report_example_values(env: Path, secrets_env: Path, set_here: dict[Path, list[str]]) -> bool:
    """Say which variables still hold whatever the example suggested.

    Run after writing, and on demand against files written long ago -- which is
    the case that matters, because a deployed site is exactly the one whose
    secrets.env this script refuses to touch. `--check` exists for it.

    A value equal to the example is not necessarily wrong: a lab is *meant* to
    run on lab credentials, and 127.0.0.1 is both a sane default and what the
    example suggests. So this reports rather than judges, and leaves the
    exit code to the caller.
    """
    credentials, others = [], []
    for target, example in ((secrets_env, "secrets.env.example"), (env, ".env.example")):
        if not target.exists():
            continue
        for variable in still_at_example_values(HERE / example, target, set_here.get(target, [])):
            (credentials if CREDENTIAL.search(variable) else others).append(
                f"{variable} ({target.name})"
            )

    if credentials:
        print("\n  still the example's value, and needs changing:")
        for entry in credentials:
            print(f"    {entry}")
    if others:
        # Reported without alarm. A port, a worker count or a bind address
        # equal to the example is the example agreeing with a sane default,
        # not something anybody forgot.
        print(f"\n  unchanged from the example, which is usually fine: "
              f"{', '.join(e.split(' ')[0] for e in others)}")
    return bool(credentials)


def check(env: Path, secrets_env: Path) -> int:
    """Inspect an existing configuration without writing anything."""
    missing = [p.name for p in (env, secrets_env) if not p.exists()]
    if missing:
        print(f"not configured yet: {', '.join(missing)} missing", file=sys.stderr)
        print("  run this script without --check to create them", file=sys.stderr)
        return 1

    print(f"checking {env.name} and {secrets_env.name} in {HERE}")
    # Nothing was set by this run, so every variable is compared.
    unedited = report_example_values(env, secrets_env, {})
    if not unedited:
        # Not "every value differs" -- plenty legitimately do not, and saying
        # so would be false. What matters is that no *credential* is still the
        # example's.
        print("  no credential is still at its example value")

    problems = check_prerequisites()
    if problems:
        print("\n  this host is not ready to run containers:")
        for problem in problems:
            print(f"    - {problem}")

    if unedited or problems:
        print("\nsomething above needs attention.")
        return 1
    print("\nnothing to report.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Configure a freshly cloned podpack site on this host.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Run it once")[1].split("Standard library")[0].strip(),
    )
    parser.add_argument("--port", type=int,
                        help="the port this host allocated for the site "
                             "(required unless --check)")
    parser.add_argument("--check", action="store_true",
                        help="inspect the existing configuration and write "
                             "nothing: which values are still the example's, "
                             "and whether this host can run containers")
    parser.add_argument("--site-name", default=HERE.name,
                        help="compose project and image name (default: this directory)")
    parser.add_argument("--data-dir", default="./hostdata")
    parser.add_argument("--log-dir", default="./hostlogs")
    parser.add_argument("--db-name", default=None,
                        help="database name (default: derived from --site-name)")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="address to publish the site on (default: loopback, "
                             "which is right when a proxy runs on this host)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing files -- see the warning above")
    args = parser.parse_args()

    env, secrets_env = HERE / ".env", HERE / "secrets.env"

    if args.check:
        return check(env, secrets_env)
    if args.port is None:
        parser.error("--port is required (or use --check to inspect what exists)")
    existing = [p.name for p in (env, secrets_env) if p.exists()]
    if existing and not args.force:
        print(f"refusing to overwrite: {', '.join(existing)}", file=sys.stderr)
        print(
            "  These carry a live site's identity -- the salt every stored\n"
            "  password is keyed on, and the role the database was created\n"
            "  with. Regenerating them does not reconfigure the site, it\n"
            "  loses it. Use --force only on a site with no data yet.",
            file=sys.stderr,
        )
        print("\n  Nothing was changed. What is there now:", file=sys.stderr)
        report_example_values(env, secrets_env, {})
        print("\n  `--check` reports this without the refusal.", file=sys.stderr)
        return 1

    db = args.db_name or re.sub(r"[^a-z0-9_]", "_", args.site_name.lower())
    app_user, app_password = f"{db}_app", generated()
    relabel = selinux_is_enforcing()

    wrote_env = fill(HERE / ".env.example", env, {
        "SITE_NAME": args.site_name,
        "HOST_DATA_DIR": args.data_dir,
        "HOST_LOG_DIR": args.log_dir,
        "WEB_HOST_PORT": str(args.port),
        "WEB_BIND_ADDR": args.bind,
        "GUNICORN_WORKERS": str(args.workers),
        "COMPOSE_FILE": "compose.yaml:compose.postgres.yaml",
        # Detected, not asked: getting this wrong produces a healthy-looking
        # PostgreSQL whose db-init never ran, and a migrate service that
        # cannot authenticate three steps downstream.
        "VOLUME_RW": ":Z" if relabel else "",
        "VOLUME_RO": ",z" if relabel else "",
    })

    # The URI is *built* from the parts rather than sitting beside them,
    # because the two drifting apart is a genuine failure mode: the role is
    # created once from POSTGRES_APP_USER/PASSWORD at first bootstrap, and the
    # application connects with whatever the URI says.
    wrote = fill(HERE / "secrets.env.example", secrets_env, {
        "POSTGRES_USER": f"{db}_admin",
        "POSTGRES_PASSWORD": generated(),
        "POSTGRES_DB": db,
        "POSTGRES_APP_USER": app_user,
        "POSTGRES_APP_PASSWORD": app_password,
        "SQLALCHEMY_DATABASE_URI":
            f"postgresql+psycopg2://{app_user}:{app_password}@postgres:5432/{db}",
        "SECRET_KEY": generated(),
        "SECURITY_PASSWORD_SALT": generated(),
    })

    print(f"wrote .env and secrets.env (0600) for {args.site_name!r}")
    print(f"  port          {args.port} on {args.bind}")
    print(f"  database      {db}, application role {app_user}")
    print(f"  SELinux       {'detected -- mounts will be relabelled' if relabel else 'not enforcing'}")
    print("  secrets       generated; none printed here, and none of them lab values")

    # Anything this script had no opinion about kept the example's value. For a
    # site's own credential -- holdenweb.com's MAIL_PASSWORD, say -- that is a
    # lab value masquerading as a real one.
    report_example_values(env, secrets_env, {secrets_env: wrote, env: wrote_env})

    problems = check_prerequisites()
    if problems:
        print("\nthis host is not ready to run containers yet:")
        for problem in problems:
            print(f"  - {problem}")
    print("\nnext: ./scripts/prepare-host-dirs.sh && ./scripts/up.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
