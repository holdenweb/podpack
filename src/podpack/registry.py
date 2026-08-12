"""The app registry: what installing a feature into a site actually does.

Flask gives a blueprint its own views, templates, static files and CLI commands
for nothing. What it does not give is the rest of what a Django app bundles --
models that migrations can see, a declaration of what is installed, somewhere to
put per-app data, and a way to contribute to the site's navigation. That is what
this module adds, and the whole of it.

An app is a package exposing a single module-level `site_app`:

    from podpack import Section, SiteApp
    from .views import blueprint      # Blueprint("myapp", __name__, ...)

    site_app = SiteApp(
        blueprint=blueprint,
        url_prefix="/myapp",
        nav=(Section("My App", "myapp.index"),),
    )

The app's name is its blueprint's name; see `SiteApp.name`. Everything else is
convention: `models.py` if it has models, `templates/<name>/` if it has
templates, `data/` if it ships data.
"""

import logging
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from importlib import import_module
from importlib.resources import as_file, files
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType
from typing import Any

from flask import Blueprint, Flask
from sqlalchemy.exc import InvalidRequestError

from .nav import Section
from .paths import attach_file_logging, prepare

logger = logging.getLogger(__name__)

# SQLAlchemy's wording for a second definition of one table name. Matched rather
# than depended on: see `_clashing_table` for what happens when it changes.
_ALREADY_DEFINED = re.compile(r"Table '([^']+)' is already defined")


@dataclass(frozen=True)
class SiteApp:
    """An installable unit of site functionality."""

    blueprint: Blueprint
    url_prefix: str | None = None
    """Where the app asks to be mounted, and only asks: a site that wants it
    elsewhere in its address space says so in `[site.mounts]`, keyed by app
    name, and the registry mounts it there instead. `None` mounts at the site
    root. Nav entries need no adjustment either way, because they name
    endpoints rather than paths.

    The site's table and not the app's own config section, because where an app
    lands is a decision the app takes no part in; see ADR-0006."""

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

    host_config: dict[str, Any]
    data_root: Path
    log_root: Path
    apps: dict[str, SiteApp] = field(default_factory=dict)
    nav: list[Section] = field(default_factory=list)

    table_owners: dict[str, str] = field(default_factory=dict)
    """Table name to the app that put it on `db.metadata`.

    The one namespace shared across installed apps, so the only one where "which
    app owns this?" is a question that needs asking. `/_status` reports it.
    """

    installed_from: dict[str, str] = field(default_factory=dict)
    """App name to the import name it was installed from.

    The two are routinely different -- a distribution wants a namespaced name
    while an app wants a short one to put in URLs, template paths and
    directories, so `podpack_myapp` may well answer to `myapp`. Keeping
    the mapping lets `/_status` report it, so which name a site should key
    `[site.mounts]` and `[apps.<name>]` on is answerable by looking rather than
    by getting it wrong first.
    """


def install_apps(app: Flask, names: Iterable[str]) -> None:
    """Install each named app into `app`, in the order given.

    Order is the site's to choose, and it matters: nav entries appear in
    installation order, and an app's `init` may depend on a service another has
    already registered.
    """
    state = app.extensions["podpack"]
    for name in names:
        site_app = _install(app, state, name)
        state.apps[site_app.name] = site_app
        state.installed_from[site_app.name] = name
    _check_mounts(state)


def _install(app: Flask, state: PodpackState, module_name: str) -> SiteApp:
    site_app = _import_app(module_name, state.table_owners)
    if site_app.name in state.apps:
        raise RuntimeError(
            f"two installed apps share the blueprint name {site_app.name!r}; it "
            "has to be unique because it decides the template namespace, the "
            "data and log directories, and the config section"
        )

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


def _check_mounts(state: PodpackState) -> None:
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


def _reject_mount_in_app_config(state: PodpackState, name: str) -> None:
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


def import_app_models(names: Iterable[str]) -> None:
    """Import every named app's models, and nothing else about the apps.

    The migration environment needs an app's tables on `db.metadata` but has no
    use for its blueprint, its templates or its data directory -- and no way to
    create the last of those, since it runs without a Flask app. Keeping this
    separate from `install_apps` is what lets alembic work without one.

    It holds every app to the same contract even so, and the reason is the shape
    of the compose stack rather than anything about migrations. `migrate` runs
    before `web` and gates it with `service_completed_successfully`. While this
    accepted a module with no `site_app`, that gate passed, and the site's real
    failure surfaced one service later in `web` -- so the logs blamed the thing
    that was merely next. The check needs no Flask app, so ADR-0010 is untouched.
    """
    owners: dict[str, str] = {}
    for name in names:
        _import_app(name, owners)


def _import_app(module_name: str, table_owners: dict[str, str]) -> SiteApp:
    """Import an app, check the contract, and record the tables it contributed.

    Importing the app *is* model registration: defining a `db.Model` subclass
    registers it on `db.metadata` as an import side effect. `models.py` is
    imported explicitly because it is the one module the convention promises to
    reach even when nothing in the package imports it.

    The tables are recorded because `db.metadata` is the one namespace podpack
    does not divide by app -- templates, data and log directories and config
    sections all carry the app's name, table names do not. SQLAlchemy refuses
    the second app to claim a name, which is right, but its error identifies the
    table and neither of the two apps responsible; knowing who claimed what is
    the whole of the difference between that and a message worth reading.
    """
    try:
        module = import_module(module_name)
        site_app = _site_app(module_name, module)
        _import_if_present(f"{module_name}.models")
    except InvalidRequestError as exc:
        raise RuntimeError(_clashing_table(module_name, table_owners, exc)) from exc

    for table in sorted(_tables_declared_by(module_name)):
        table_owners[table] = site_app.name
        if not table.startswith(site_app.name):
            # Warned here rather than checked at the clash, because the clash
            # happens on whichever site installs both apps -- by which time the
            # author who could fix it is not the person reading the error.
            logger.warning(
                "app %r declares the table %r, which its own name does not "
                "prefix. Table names are shared across every installed app, so "
                "a second app claiming %r will stop a site booting.",
                site_app.name,
                table,
                table,
            )
    return site_app


def _tables_declared_by(module_name: str) -> set[str]:
    """The tables whose models were defined inside this app's package.

    Asked of the mapper registry rather than measured as what an import added to
    `db.metadata`, because that metadata belongs to the process and not to the
    site: build a second site in one interpreter -- which every test suite does,
    and no deployment does -- and the modules are already imported, so the second
    site's apps appear to have contributed nothing. Attribution by defining
    module gives the same answer however many times a site is built.
    """
    from . import db

    prefix = f"{module_name}."
    # flask-sqlalchemy types `db.Model` with a protocol that does not declare
    # `registry`, though the declarative base it builds at runtime carries one.
    registry = db.Model.registry  # type: ignore[attr-defined]
    return {
        mapper.local_table.name
        for mapper in registry.mappers
        if mapper.local_table is not None
        and (mapper.class_.__module__ == module_name or mapper.class_.__module__.startswith(prefix))
    }


def _site_app(module_name: str, module: ModuleType) -> SiteApp:
    """The app's `site_app`, or an error saying which way it is wrong."""
    site_app = getattr(module, "site_app", None)
    if site_app is None:
        raise RuntimeError(
            f"{module_name} is listed as an installed app but exposes no "
            "module-level `site_app`; see podpack.registry for the contract"
        )
    # Separately from the absent case, because the two were one check and its
    # message described only the first. An app packaged as a plain blueprint --
    # `site_app = blueprint`, the natural move for anyone coming from
    # register_blueprint -- was told it exposed no `site_app` while the name sat
    # in plain sight in its module, which sends the reader looking for a missing
    # line rather than a wrong type.
    if not isinstance(site_app, SiteApp):
        raise RuntimeError(
            f"{module_name}.site_app is a {type(site_app).__name__}, not a "
            "SiteApp. A podpack app wraps its blueprint rather than exporting "
            "it: `site_app = SiteApp(blueprint=..., url_prefix=...)`; see "
            "podpack.registry for the contract"
        )
    return site_app


def _clashing_table(
    module_name: str, table_owners: dict[str, str], exc: InvalidRequestError
) -> str:
    """Name both apps in a table-name collision, where SQLAlchemy names neither.

    The table is read out of SQLAlchemy's message because the import that would
    have told us aborted part way through. A miss is survivable -- the apps
    installed so far and what they claimed is still more than the original error
    said -- so this reports what it has rather than insisting on a match.
    """
    match = _ALREADY_DEFINED.search(str(exc))
    table = match.group(1) if match else None
    if table is not None and table in table_owners:
        clash = f"the table {table!r}, which {table_owners[table]!r} already claims"
    elif table is not None:
        clash = f"the table {table!r}, which is already on db.metadata"
    else:
        claimed = ", ".join(f"{t} ({a})" for t, a in sorted(table_owners.items()))
        clash = f"a table already on db.metadata; claimed so far: {claimed or '(none)'}"
    return (
        f"installing {module_name!r} failed: it declares {clash}. Table names "
        "are shared across every installed app -- unlike templates, data "
        "directories and config sections, which podpack namespaces by app name "
        "-- so prefix __tablename__ with the app's own name."
    )


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
