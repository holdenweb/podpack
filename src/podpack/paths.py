"""Per-app data and log directories.

Every installed app gets a subdirectory of the host-mounted roots, named after
the app:

    <data root>/<app name>/     persistent data the app owns
    <log root>/<app name>/      logs the app writes

Two things follow, and both are the point of the convention. Installing an app
never requires a change to `compose.yaml`, because the *roots* are mounted and
the per-app directories are created inside them at startup. And nothing needs
relocating at deployment, because moving to a real host moves the roots -- which
is already a `.env` edit and nothing more.

Apps resolve their directories through these helpers rather than by building
paths themselves, so that the layout stays the framework's business.
"""

import logging
import pathlib

from flask import current_app, request


def data_root():
    """The root under which every app's data directory is created."""
    return current_app.extensions["podpack"].data_root


def log_root():
    """The root under which every app's log directory is created."""
    return current_app.extensions["podpack"].log_root


def data_dir(name=None):
    """The calling app's persistent data directory on the host.

    `name` defaults to the blueprint handling the current request, so an app's
    view code can simply call `data_dir()`.
    """
    return data_root() / (name or _current_app_name())


def log_dir(name=None):
    """The calling app's log directory on the host."""
    return log_root() / (name or _current_app_name())


def _current_app_name():
    """The installed app handling this request.

    A plugin's blueprint is registered under the app's own name, so the
    blueprint currently in play identifies the app without the view having to
    repeat it.
    """
    name = request.blueprint
    if name is None:
        raise RuntimeError(
            "data_dir()/log_dir() were called outside any blueprint, so there "
            "is no app to resolve; pass the app name explicitly"
        )
    return name


def prepare(root: pathlib.Path, name: str) -> pathlib.Path:
    """Create and return `<root>/<name>`, which the app then owns.

    Created at startup rather than lazily so that a permissions problem shows up
    when the container boots -- next to the other mount checks -- rather than on
    whichever request first happens to write something.
    """
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def unclaimed(root: pathlib.Path, installed) -> list[str]:
    """Entries under `root` that no installed app answers for.

    The roots are supposed to hold one subdirectory per installed app and
    nothing else. They drift anyway, and legitimately: removing an app from the
    site's `apps` list deliberately does *not* delete its data, because
    uninstalling a feature should not destroy what it was holding. So a
    directory outstays its app, and until it is reported the disk and
    `/_status` quietly disagree about what the site consists of.

    Reported rather than removed. Deleting data because a config line changed
    would be the wrong instinct entirely -- the point is to make what is there
    visible, and leave the decision where it belongs.

    Files are listed as well as directories: an app's name buys it a directory,
    so anything else at this level is equally unaccounted for.
    """
    try:
        entries = list(root.iterdir())
    except OSError:
        # The root need not exist yet -- a site with no apps never creates one.
        return []
    return sorted(entry.name for entry in entries if entry.name not in installed)


def attach_file_logging(module_name: str, directory: pathlib.Path, filename: str):
    """Send an app's log records to its own file as well as to stdout.

    The handler goes on the *package's* logger, so an app that does nothing more
    than `logging.getLogger(__name__)` gets file logging for free: its records
    propagate up to the package logger on their way to the root.
    """
    try:
        handler = logging.FileHandler(directory / filename)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "file logging disabled for %s: %s", module_name, exc
        )
        return
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s")
    )
    logger = logging.getLogger(module_name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
