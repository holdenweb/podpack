"""Configuration in two layers: a host file, and the environment.

The split is the one the container substrate is built around. Anything that
varies between laptop, staging and production but is *not* a secret lives in a
bind-mounted, read-only TOML file, where it can be version-controlled and
reviewed; secrets arrive through the environment, where they leave no trace on
disk. Promotion to a real host is then an edit of `.env` alone.

Both functions refuse to carry on when something is missing. Starting with
silent defaults would mean a wrong bind mount or a forgotten variable showing up
later as puzzling behaviour rather than immediately as a failure to boot.
"""

import os
import pathlib
import re
import tomllib
from collections.abc import Mapping
from typing import Any

from flask import current_app

from .paths import _current_app_name

DEFAULT_CONFIG_PATH = pathlib.Path(
    os.environ.get("PODPACK_CONFIG", "/etc/holdenweb/app.toml")
)


def load_host_config(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Read the host-supplied TOML config, failing loudly if it is missing.

    Refusing to start beats starting with silent defaults: a missing file here
    means the bind mount is wrong, which is exactly what the container suite
    exists to catch before the same compose file reaches a real host.
    """
    path = pathlib.Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.is_file():
        raise RuntimeError(
            f"host configuration not mounted at {path} -- check the "
            "./config bind mount in compose.yaml"
        )
    with path.open("rb") as stream:
        return tomllib.load(stream)


def require_env(name: str) -> str:
    """Return an environment variable, or explain which one is missing.

    Prefer `check_secrets` for anything checked at boot: this reports one name
    at a time, and a site missing three secrets learns about them across three
    restarts.
    """
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(f"required environment variable {name} is not set") from None


FRAMEWORK_SECRETS = ("SECRET_KEY", "SQLALCHEMY_DATABASE_URI", "SECURITY_PASSWORD_SALT")
"""What podpack itself cannot start without.

`SECURITY_PASSWORD_SALT` joined the list when login became core (ADR-0033), and
the manner of its joining is why `check_secrets` exists: it was missing on a
site that started perfectly and failed hours later, inside a CLI command,
naming a setting its owner had never heard of.
"""

# Delivered by `podpack substrate` in place of a value it cannot know, and by
# the example files for a site to replace. Either reaching a running site means
# a file was installed and never edited -- which is not a value, it is the
# absence of one wearing a value's clothes.
_PLACEHOLDERS = ("CHANGEME",)
_UNRESOLVED_TOKEN = re.compile(r"@@[A-Z_]+@@")


def check_secrets(required: Mapping[str, str]) -> None:
    """Fail if any required secret is missing or was never edited.

    `required` maps each environment variable to whoever says it is needed --
    "podpack", "[site] secrets", "app 'notes'" -- because the useful question
    when a deployment stops is not only *which* secret but *why anything wants
    it*, and the answer is what tells you whether to supply it or to stop
    installing the thing that asked.

    Everything at once, deliberately. Checking one at a time costs a restart
    per secret, and on a containerised deployment a restart is a rebuild, so a
    site three secrets short learns that over three cycles rather than in one
    message.

    An empty value counts as missing, because it is: an env file with
    `SECRET_KEY=` in it is not configured, whatever the shell thinks.
    """
    missing, unedited = [], []
    for name, who in required.items():
        value = os.environ.get(name, "")
        if not value.strip():
            missing.append(f"{name} (needed by {who})")
        elif any(mark in value for mark in _PLACEHOLDERS) or _UNRESOLVED_TOKEN.search(value):
            unedited.append(f"{name} (needed by {who})")

    problems = []
    if missing:
        problems.append(f"not set: {', '.join(missing)}")
    if unedited:
        problems.append(
            f"still holding a placeholder: {', '.join(unedited)} -- "
            "the file was installed and never edited"
        )
    if problems:
        raise RuntimeError(
            "this site cannot start: " + "; ".join(problems) + ". "
            "Secrets come from the environment, which compose fills from "
            "secrets.env; a local run gets them from dev.env via scripts/dev.sh."
        )


def framework_secrets(host_config: dict[str, Any]) -> dict[str, str]:
    """What podpack needs, plus what the site declares in `[site] secrets`.

    Checkable before a single app is imported, which is why it is separate from
    the apps' own: these are what `create_app` itself is about to read.
    """
    required = dict.fromkeys(FRAMEWORK_SECRETS, "podpack")
    required.update(dict.fromkeys(site_secrets(host_config), "[site] secrets"))
    return required


def installed_apps(host_config: dict[str, Any]) -> list[str]:
    """The site's app list, which is configuration rather than code.

    It lives in the host config file and not in the environment because it is
    emphatically not a secret, and because a site's identity is mostly this
    list: reviewing a deployment should mean reading a file, not decoding a
    process environment.
    """
    return list(host_config.get("site", {}).get("apps", []))


def site_secrets(host_config: dict[str, Any]) -> list[str]:
    """Environment variables this site needs beyond podpack's own.

        [site]
        secrets = ["MAIL_PASSWORD"]

    *Names* in the config file and *values* in the environment, which is the
    same split ADR-0018 makes everywhere else: what a site requires is
    reviewable and belongs in version control, while what it is stays out of
    both. Listing one here gets it checked at boot with the framework's own,
    instead of surfacing when a visitor asks for a password reset.
    """
    return list(host_config.get("site", {}).get("secrets", []))


def app_config(name: str | None = None) -> dict[str, Any]:
    """An installed app's own section of the host config file.

    Apps get a namespace of their own, so that a site can tune one without
    touching another and without the framework having to know what any of the
    settings mean:

        [apps.myapp]
        page_size = 20

    `name` defaults to the app handling the current request, so view code can
    simply call `app_config()`.
    """
    state = current_app.extensions["podpack"]
    apps = state.host_config.get("apps", {})
    return apps.get(name or _current_app_name(), {})
