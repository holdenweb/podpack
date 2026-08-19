#!/usr/bin/env python3
"""Publish this package, or refuse and say why.

`uv publish` uploads `dist/*`. That directory accumulates, so it will happily
ship an artefact from three versions ago alongside the one you just built --
and this repository was in exactly that state an hour before this script was
written: `dist/` held podpack-0.4.0 while `pyproject.toml` said 0.7.3. Running
`uv publish` then would have published 0.4.0, and PyPI does not let you take a
version back.

So this builds into a clean `dist/` and then checks, before uploading, that
what is there is what you meant:

  * exactly one version present -- the guard that prompted this, and a hard
    refusal rather than a warning, because a stale wheel beside a fresh one is
    indistinguishable from a correct build until it is on the index;
  * that version matches pyproject.toml;
  * the working tree is clean, so what ships is what is committed;
  * the release tag exists for it;
  * it is not already on PyPI -- a sentence beats a 400.

Not in `scripts/`: that holds only what `podpack substrate` manages, and this
is repo tooling. Apps can copy it; nothing here is podpack-specific beyond the
tag convention.

    export UV_PUBLISH_TOKEN=pypi-...
    python3 tools/publish.py --dry-run     # every check, no upload
    python3 tools/publish.py

The token goes in the environment rather than an argument, because arguments
are visible in `ps` to everyone on the machine. `uv publish` reads
UV_PUBLISH_TOKEN itself, so nothing here ever handles the value.

It does not prompt, incidentally -- it attempts trusted publishing, fails,
builds, hashes the wheel, and only then reports "Missing credentials" behind a
stack of OIDC connection errors. Hence the credential check below, which is
about arriving at that news sooner and in one line.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

def artefact_version(filename: str, name: str) -> str | None:
    """The version a built file claims, or None if it is not one of ours.

    Built from the *name* rather than a general pattern, because a package
    called `podpack-qrcode` has a hyphen in it and a pattern assuming the first
    hyphen ends the name would read its version as "qrcode". Distributions
    normalise the name with underscores in filenames, so both spellings are
    accepted.
    """
    for spelling in {name, name.replace("-", "_")}:
        if filename.startswith(f"{spelling}-"):
            rest = filename[len(spelling) + 1:]
            if rest.endswith(".whl"):
                return rest.split("-")[0]
            if rest.endswith(".tar.gz"):
                return rest[: -len(".tar.gz")]
    return None


def run(*command: str) -> str:
    """Run a command and return its output, for the checks."""
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def hand_over(*command: str) -> int:
    """Run a command on *this* terminal, and do not capture a thing.

    `uv publish` asks for credentials when no token is configured, and a prompt
    written to a captured pipe is a prompt nobody can see or answer: the
    command appears to hang, and the reason is invisible by construction. So
    stdin, stdout and stderr are inherited, which is the whole difference
    between this and `run`.
    """
    return subprocess.run(command, cwd=ROOT).returncode


def declared() -> tuple[str, str]:
    """This package's name and version, from the file that decides both.

    Read rather than hard-coded so that one identical copy of this script
    serves every repository in the family. Five diverging copies is how the
    .dockerignore fix came to be applied by hand three times.
    """
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    return str(project["name"]), str(project["version"])


def versions_in_dist(name: str) -> dict[str, list[str]]:
    """Every version present, and the files claiming it."""
    found: dict[str, list[str]] = {}
    for path in sorted(DIST.glob("*")):
        version = artefact_version(path.name, name)
        if version:
            found.setdefault(version, []).append(path.name)
    return found


def already_on_pypi(name: str, version: str) -> bool:
    """Whether this exact version is published.

    A miss here is survivable -- the upload fails with PyPI's own message --
    so a network problem must not stop a release. It is a courtesy, not a gate.
    """
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{name}/{version}/json", timeout=10
        ) as response:
            return bool(response.status == 200)
    except urllib.error.HTTPError as exc:
        return exc.code != 404
    except OSError:
        print("  (could not reach PyPI to check; carrying on)")
        return False


def credential_configured() -> bool:
    """Whether `uv publish` has something to authenticate with.

    Only the environment is inspected. uv also accepts a keyring and trusted
    publishing, neither of which can be detected cheaply or reliably from
    here -- hence `--assume-credentials` rather than a guess that would refuse
    a perfectly good CI run.
    """
    return bool(
        os.environ.get("UV_PUBLISH_TOKEN")
        or (os.environ.get("UV_PUBLISH_USERNAME") and os.environ.get("UV_PUBLISH_PASSWORD"))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="run every check and stop before uploading")
    parser.add_argument("--assume-credentials", action="store_true",
                        help="skip the credential check -- for keyring or "
                             "trusted publishing, which cannot be detected here")
    parser.add_argument("--no-build", action="store_true",
                        help="publish what is already in dist/ -- the case the "
                             "single-version guard exists for")
    args = parser.parse_args()

    name, version = declared()
    print(f"publishing {name} {version}")

    problems: list[str] = []

    if not args.no_build:
        # A clean build makes the guard below unreachable, which is the point:
        # the guard is for the path that skips this.
        if DIST.exists():
            shutil.rmtree(DIST)
        print("  building into a clean dist/")
        run("uv", "build")          # not interactive; output is noise

    if not DIST.is_dir() or not any(DIST.iterdir()):
        problems.append("dist/ is empty -- nothing to publish")
    else:
        found = versions_in_dist(name)
        if len(found) > 1:
            listing = "; ".join(
                f"{seen}: {', '.join(files)}" for seen, files in sorted(found.items())
            )
            problems.append(
                f"dist/ holds more than one version ({listing}). `uv publish` "
                "uploads all of them and PyPI keeps whatever it accepts. "
                "Remove the ones you did not mean, or drop --no-build."
            )
        elif found and version not in found:
            problems.append(
                f"dist/ holds {', '.join(found)} but pyproject.toml says {version}"
            )

    if run("git", "status", "--porcelain"):
        problems.append("the working tree is dirty -- publish what is committed")

    tag = f"r{version}"
    if tag not in run("git", "tag", "--list").splitlines():
        problems.append(f"no {tag} tag -- tag the release before publishing it")

    if already_on_pypi(name, version):
        problems.append(f"{name} {version} is already on PyPI, and versions are final")

    if not args.assume_credentials and not credential_configured():
        # Checked here rather than left to uv, which tries trusted publishing,
        # fails, builds, hashes the whole wheel and only then says "Missing
        # credentials" -- by which point the interesting line has scrolled off
        # behind a stack of OIDC connection errors. Measured against a dead
        # endpoint, which is how this check came to exist.
        problems.append(
            "no PyPI credential in the environment. Create a token at "
            "pypi.org (Account settings -> API tokens) and export it:\n"
            "        export UV_PUBLISH_TOKEN=pypi-...\n"
            "      Scope it to this project once the project exists -- PyPI "
            "only offers a project scope for projects you already own, so a\n"
            "      first upload needs an account-scoped token you then revoke.\n"
            "      Using keyring or trusted publishing instead? "
            "--assume-credentials"
        )

    if problems:
        print("\nrefusing to publish:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("  every check passed")
    if args.dry_run:
        print("(dry run: nothing uploaded)")
        return 0

    # Everything above was a check; this is the part that reaches the world.
    print("\n  handing over to `uv publish`\n")
    code = hand_over("uv", "publish")
    if code != 0:
        print(f"\n`uv publish` exited {code}: nothing was published", file=sys.stderr)
        return code
    print(f"\npublished {name} {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
