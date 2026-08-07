"""The app registry: what installing a feature into a site actually does.

Flask gives a blueprint its own views, templates, static files and CLI commands
for nothing. What it does not give is the rest of what a Django app bundles --
models that migrations can see, a declaration of what is installed, somewhere to
put per-app data, and a way to contribute to the site's navigation. That is what
this module adds, and the whole of it.

An app is a package exposing a single module-level `site_app`:

    from podpack import Section, SiteApp
    from .views import blueprint      # Blueprint("notes", __name__, ...)

    site_app = SiteApp(
        blueprint=blueprint,
        url_prefix="/notes",
        nav=(Section("Notes", "notes.index"),),
    )

The app's name is its blueprint's name; see `SiteApp.name`. Everything else is
convention: `models.py` if it has models, `templates/<name>/` if it has
templates, `data/` if it ships data.
"""

import shutil
from dataclasses import dataclass, field, replace
from importlib import import_module
from importlib.resources import as_file, files
from importlib.util import find_spec
from pathlib import Path
from typing import Callable

from flask import Blueprint, Flask

from .nav import Section
from .paths import attach_file_logging, prepare


@dataclass(frozen=True)
class SiteApp:
    """An installable unit of site functionality."""

    blueprint: Blueprint
    url_prefix: str | None = None
    """Where the app asks to be mounted, and only asks: a site that wants it
    elsewhere in its address space says so with `url_prefix` in the app's own
    config section, and the registry mounts it there instead. `None` mounts at
    the site root. Nav entries need no adjustment either way, because they name
    endpoints rather than paths."""

    nav: tuple[Section, ...] = ()

    init: Callable[[Flask], None] | None = None
    """Called before the blueprint is registered, for config keys and services
    the app needs. The site's own config is already loaded by this point, so an
    app can read its section of the host config file here."""

    @property
    def name(self) -> str:
        """Identifies the app everywhere it needs identifying: its template
        namespace, its data and log directories, and its section of the site's
        config file.

        Derived rather than declared, because a declared copy could only drift.
        The blueprint's name is already this app's public identity -- it
        prefixes every endpoint, and so appears in every `url_for` and every nav
        entry -- and it is what podpack resolves an app from at runtime, through
        `request.blueprint`. When these were two fields that had to agree,
        nothing detected them disagreeing: the registry would create one
        directory and read one config section while the views used another, with
        no error at boot or in the request.
        """
        return self.blueprint.name


@dataclass
class PodpackState:
    """Everything podpack knows about one running site.

    Stashed at `app.extensions["podpack"]` -- the same per-app registry pattern
    the site already uses for its content source, so there is one answer to
    "where does a request find its services" rather than two.
    """

    host_config: dict
    data_root: Path
    log_root: Path
    apps: dict[str, SiteApp] = field(default_factory=dict)
    nav: list[Section] = field(default_factory=list)


def install_apps(app: Flask, names) -> None:
    """Install each named app into `app`, in the order given.

    Order is the site's to choose, and it matters: nav entries appear in
    installation order, and an app's `init` may depend on a service another has
    already registered.
    """
    state = app.extensions["podpack"]
    for name in names:
        site_app = _install(app, state, name)
        state.apps[site_app.name] = site_app
    _check_mounts(state)


def _install(app: Flask, state: PodpackState, module_name: str) -> SiteApp:
    module = import_module(module_name)
    site_app = getattr(module, "site_app", None)
    if not isinstance(site_app, SiteApp):
        raise RuntimeError(
            f"{module_name} is listed as an installed app but exposes no "
            "module-level `site_app`; see podpack.registry for the contract"
        )
    if site_app.name in state.apps:
        raise RuntimeError(
            f"two installed apps share the blueprint name {site_app.name!r}; it "
            "has to be unique because it decides the template namespace, the "
            "data and log directories, and the config section"
        )

    # Importing the models module *is* model registration: defining a db.Model
    # subclass registers it on db.metadata as an import side effect. It happens
    # here, at install time, because alembic reads that metadata after building
    # an app -- so an app whose models were imported lazily would be invisible
    # to autogenerate and its tables would silently never be created.
    _import_if_present(f"{module_name}.models")

    data_dir = prepare(state.data_root, site_app.name)
    log_dir = prepare(state.log_root, site_app.name)
    _seed_data(module_name, data_dir)
    attach_file_logging(module_name, log_dir, f"{site_app.name}.log")

    if site_app.init is not None:
        site_app.init(app)

    # An app's `url_prefix` is a request, not a claim on the site's address
    # space: the site may put it somewhere else entirely. `replace` rather than
    # a local variable so that what goes into `state.apps` -- and so what
    # /_status reports -- is where the app actually ended up.
    _reject_mount_in_app_config(state, site_app.name)
    mounts = state.host_config.get("site", {}).get("mounts", {})
    if site_app.name in mounts:
        site_app = replace(site_app, url_prefix=mounts[site_app.name])

    app.register_blueprint(site_app.blueprint, url_prefix=site_app.url_prefix)
    _check_nav(app, site_app)
    state.nav.extend(site_app.nav)
    return site_app


def _check_mounts(state) -> None:
    """Refuse to boot on a mount for an app that is not installed.

    Keeping mounts in their own table costs one thing that keeping them inside
    `[apps.<name>]` did not: the two can drift. A typo, or an app dropped from
    `apps` while its mount stayed behind, would otherwise be silent -- and the
    app would come up at the address it asked for, which is precisely the
    address the site said it did not want. Checked after installation because
    that is the first moment the app names are known.
    """
    mounts = state.host_config.get("site", {}).get("mounts", {})
    unknown = set(mounts) - set(state.apps)
    if unknown:
        installed = ", ".join(sorted(state.apps)) or "(none)"
        raise RuntimeError(
            f"[site.mounts] mounts {', '.join(sorted(unknown))}, which no "
            f"installed app answers to. Installed apps: {installed}. Note the "
            "key is the app's name -- that is, its blueprint's name -- which is "
            "not always the import name listed in `apps`."
        )


def _reject_mount_in_app_config(state, name: str) -> None:
    """Refuse to boot on the old spelling rather than quietly ignoring it.

    Mounting used to be configured with `url_prefix` in the app's own
    `[apps.<name>]` table. Moving it to `[site.mounts]` would otherwise be a
    silent downgrade for any site still using the old form: the app would come
    up at the address it asked for rather than the one the site chose, with
    nothing said. Failing here costs one edit and names it exactly.
    """
    if "url_prefix" in state.host_config.get("apps", {}).get(name, {}):
        raise RuntimeError(
            f"[apps.{name}] sets url_prefix, which podpack no longer reads. "
            f"Mount points belong to the site, so move it to:\n\n"
            f"    [site.mounts]\n    {name} = \"…\"\n\n"
            f"`[apps.{name}]` is for settings the app itself reads."
        )


def _check_nav(app: Flask, site_app: SiteApp) -> None:
    """Refuse to boot if a nav entry names an endpoint no view provides.

    The chrome resolves these with `url_for` as it renders, so one bad entry
    raises BuildError on *every* page of the site rather than 404ing on the one
    page it points at. A boot failure naming the app is a great deal easier to
    act on, and it is checked here because this is the first moment the app's
    routes exist.
    """
    for section in site_app.nav:
        if section.endpoint not in app.view_functions:
            raise RuntimeError(
                f"{site_app.name} contributes the nav entry {section.label!r} "
                f"naming the endpoint {section.endpoint!r}, which no view "
                "provides; nav entries name endpoints rather than paths so "
                "that they follow the app wherever the site mounts it"
            )


def import_app_models(names) -> None:
    """Import every named app's models, and nothing else about the apps.

    The migration environment needs an app's tables on `db.metadata` but has no
    use for its blueprint, its templates or its data directory -- and no way to
    create the last of those, since it runs without a Flask app. Keeping this
    separate from `install_apps` is what lets alembic work without one.
    """
    for name in names:
        import_module(name)
        _import_if_present(f"{name}.models")


def _import_if_present(module_name: str) -> None:
    """Import a module only if the app actually has one.

    `find_spec` rather than catching ImportError, so that a genuine failure
    *inside* an app's models -- a typo, a missing dependency -- still raises
    instead of being mistaken for "this app has no models".
    """
    try:
        if find_spec(module_name) is None:
            return
    except ModuleNotFoundError:
        return
    import_module(module_name)


def _seed_data(module_name: str, target: Path) -> None:
    """Copy an app's shipped `data/` to the host, the first time only.

    An app can bring data with it, but the copy it runs against has to live on
    the host: that is where backups, editing and deployment expect it, and the
    package itself is read-only inside the image.

    Seeding is skipped once the target has anything in it, which gives the same
    semantics as the database bootstrap in `db-init/` -- "the first time on this
    machine", not "every time the container is recreated". Re-arming it means
    deleting the app's host data directory, exactly as re-arming that one means
    deleting the cluster.
    """
    if any(target.iterdir()):
        return
    try:
        source = files(module_name) / "data"
    except (ModuleNotFoundError, TypeError):
        return
    if not source.is_dir():
        return
    with as_file(source) as path:
        shutil.copytree(path, target, dirs_exist_ok=True)
