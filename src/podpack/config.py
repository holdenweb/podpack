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
import tomllib
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
    """Return an environment variable, or explain which one is missing."""
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(f"required environment variable {name} is not set") from None


def installed_apps(host_config: dict[str, Any]) -> list[str]:
    """The site's app list, which is configuration rather than code.

    It lives in the host config file and not in the environment because it is
    emphatically not a secret, and because a site's identity is mostly this
    list: reviewing a deployment should mean reading a file, not decoding a
    process environment.
    """
    return list(host_config.get("site", {}).get("apps", []))


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
