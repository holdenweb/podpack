#!/usr/bin/env python3
"""Provoke every failure `writing-an-app.md` documents, and print what podpack says.

The guide quotes podpack's error messages verbatim, and prose does not move
when code does: three of those quotes had drifted before this existed, two of
them by exactly the wording ADR-0034 changed when table *ownership* became
table *need*.

So this builds throwaway apps that trigger each documented failure and prints
the real text. Run it after changing any message the guide quotes, and after
adding a boot check the guide should cover:

    uv run python tools/provoke-errors.py

Comparing by eye rather than asserting, deliberately. A test asserting exact
message text would fail on every improvement to the wording, which is how
messages end up frozen badly; and grepping the source for a quoted fragment
does not work at all -- these messages interpolate (`{type(site_app).__name__}`,
`%r`) and are split across string literals, so the words the reader sees exist
nowhere in the source. Running the code is the only honest check, and this is
the cheapest way to run it.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="podpack-provoke-"))
sys.path.insert(0, str(ROOT))

# create_app insists on these, and a site that cannot boot at all would tell
# us nothing about the failures we are here to see.
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
os.environ.setdefault("SECURITY_PASSWORD_SALT", "test-password-salt")

from podpack import create_app  # noqa: E402


def make(name: str, source: str) -> str:
    (ROOT / f"{name}.py").write_text(source)
    return name


def build(apps: list[str], **overrides) -> object:
    host_config = {"site": {"name": "provoke", "environment": "test", "apps": apps}}
    for key, value in overrides.pop("host_config", {}).items():
        host_config[key] = {**host_config.get(key, {}), **value}
    return create_app(
        host_config=host_config,
        data_root=ROOT / "data",
        log_root=ROOT / "logs",
        admin=lambda: True,
        **overrides,
    )


def show(label: str, fn: Callable[[], object]) -> None:
    print(f"\n=== {label} " + "=" * max(4, 58 - len(label)))
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 -- catching everything is the point
        print(f"{type(exc).__name__}: {exc}")
    else:
        print("!! NO EXCEPTION -- this failure no longer happens, or no longer here")


APP = (
    "from flask import Blueprint\n"
    "from podpack import Section, SiteApp\n"
    "blueprint = Blueprint({name!r}, __name__)\n"
    "site_app = SiteApp(blueprint=blueprint, url_prefix='/{name}'{extra})\n"
)

make("forgotapp", "from flask import Blueprint\nblueprint = Blueprint('forgot', __name__)\n")
show("No site_app", lambda: build(["forgotapp"]))

make(
    "bare_blueprint_app",
    "from flask import Blueprint\nblueprint = Blueprint('bare', __name__)\nsite_app = blueprint\n",
)
show("site_app is a Blueprint", lambda: build(["bare_blueprint_app"]))

make("navless", APP.format(name="navless", extra=", nav=(Section('My App', 'navless.nope'),)"))
show("A nav entry pointing nowhere", lambda: build(["navless"]))

for module in ("dup_one", "dup_two"):
    make(
        module,
        "from flask import Blueprint\nfrom podpack import SiteApp\n"
        "blueprint = Blueprint('samename', __name__)\n"
        f"site_app = SiteApp(blueprint=blueprint, url_prefix='/{module}')\n",
    )
show("Two apps with the same blueprint name", lambda: build(["dup_one", "dup_two"]))

make("lonely", APP.format(name="lonely", extra=""))
show(
    "A mount for an app that is not installed",
    lambda: build(["lonely"], host_config={"site": {"mounts": {"nosuchapp": "/elsewhere"}}}),
)
show(
    "url_prefix in the app's own config section",
    lambda: build(["lonely"], host_config={"apps": {"lonely": {"url_prefix": "/nope"}}}),
)

for module, suffix in (("clash_a", "A"), ("clash_b", "B")):
    make(
        module,
        "from flask import Blueprint\nfrom podpack import SiteApp, db\n"
        f"class Thing{suffix}(db.Model):\n"
        "    __tablename__ = 'things'\n"
        "    id = db.Column(db.Integer, primary_key=True)\n"
        f"blueprint = Blueprint({module!r}, __name__)\n"
        f"site_app = SiteApp(blueprint=blueprint, url_prefix='/{module}')\n",
    )
# The unprefixed-name WARNING is logged by the first of these, before the
# second raises; both belong to the guide's models section.
show("Two apps defining one table", lambda: build(["clash_a", "clash_b"]))

make("needy", APP.format(name="needy", extra=", needs_tables=frozenset({'nobody_defines_this'})"))
show("A declared need that nothing satisfies", lambda: build(["needy"]))

make("secretive", APP.format(name="secretive", extra=", needs_secrets=frozenset({'MAPS_API_KEY'})"))
show("A secret the app needs and nobody set", lambda: build(["secretive"]))


def outside_request() -> None:
    from podpack.paths import data_dir

    app = build(["lonely"])
    with app.app_context():  # type: ignore[attr-defined]
        data_dir()


show("data_dir() in an app context, with no request", outside_request)


def outside_app() -> None:
    from podpack.paths import data_dir

    data_dir()


show("data_dir() with no application at all", outside_app)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nCompare the above with the quotes in writing-an-app.md.")
