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

    python3 tools/publish.py --dry-run     # every check, no upload
    python3 tools/publish.py
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# `name-version-...whl` and `name-version.tar.gz`: the version is the second
# hyphen-separated field of a wheel, and what follows the last hyphen before
# `.tar.gz` in an sdist.
WHEEL = re.compile(r"^[^-]+-([^-]+)-.*\.whl$")
SDIST = re.compile(r"^[^-]+-(.+)\.tar\.gz$")


def run(*command: str) -> str:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def declared_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def versions_in_dist() -> dict[str, list[str]]:
    """Every version present, and the files claiming it."""
    found: dict[str, list[str]] = {}
    for path in sorted(DIST.glob("*")):
        match = WHEEL.match(path.name) or SDIST.match(path.name)
        if match:
            found.setdefault(match.group(1), []).append(path.name)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="run every check and stop before uploading")
    parser.add_argument("--no-build", action="store_true",
                        help="publish what is already in dist/ -- the case the "
                             "single-version guard exists for")
    args = parser.parse_args()

    version = declared_version()
    name = "podpack"
    print(f"publishing {name} {version}")

    problems: list[str] = []

    if not args.no_build:
        # A clean build makes the guard below unreachable, which is the point:
        # the guard is for the path that skips this.
        if DIST.exists():
            shutil.rmtree(DIST)
        print("  building into a clean dist/")
        run("uv", "build")

    if not DIST.is_dir() or not any(DIST.iterdir()):
        problems.append("dist/ is empty -- nothing to publish")
    else:
        found = versions_in_dist()
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

    if problems:
        print("\nrefusing to publish:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("  every check passed")
    if args.dry_run:
        print("(dry run: nothing uploaded)")
        return 0

    run("uv", "publish")
    print(f"published {name} {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
