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

from .config import check_secrets
from .nav import Section
from .paths import attach_file_logging, prepare

logger = logging.getLogger(__name__)

# SQLAlchemy's wording for a second definition of one table name. Matched rather
# than depended on: see `_clashing_table` for what happens when it changes.
_ALREADY_DEFINED = re.compile(r"Table '([^']+)' is already defined")


@dataclass(frozen=True)
class Health:
    """What an app says about itself when podpack asks.

    `fatal` is opt-in and rarely right: `/healthz` gates the whole stack
    through the container healthcheck, so an app that marks its own outage
    fatal is asking for one broken feature to stop the site from serving the
    others. Say it only when the site genuinely has no purpose without you.
    """

    ok: bool
    detail: str = ""
    fatal: bool = False


@dataclass(frozen=True)
class Backup:
    """What of this app's state has to come back for the app to be whole.

    Almost always unnecessary, and deliberately so. podpack already knows
    where an app's files live -- `<data root>/<name>`, created for every app
    at install (ADR-0007) -- and reads its tables from the mapper registry,
    so an app that stores things ordinarily declares nothing here and is
    archived correctly anyway. This is for what looking cannot establish.
    """

    data: bool = True
    """Whether this app's own data directory holds anything worth keeping.

    `False` is a claim of statelessness rather than a request to skip the
    directory, and podpack checks it: a stateless app whose directory is not
    empty is reported, not quietly believed. Worth saying out loud because
    an empty directory is otherwise ambiguous -- `podpack-qrcode` holds zero
    bytes because it streams every code it makes, and a mount that never
    arrived looks exactly the same from here."""

    excludes: frozenset[str] = frozenset()
    """Subtrees of that directory holding derived data a restore can rebuild.

    Names directly beneath the app's own directory, not paths: anything
    deeper is a sign the app is managing a tree it should be managing
    through its own code."""

    extra: tuple[str, ...] = ()
    """State this app keeps outside its own directory, relative to the data
    root.

    The escape hatch, and the one declaration here to be suspicious of. An
    app that needs it is usually an app writing somewhere it should not be,
    and moving the files is nearly always cheaper than teaching every
    backup on every site about the exception."""

    reseedable: bool = False
    """Whether a fresh install would regenerate this from what the app ships.

    False for anything a person can edit, which in practice is nearly
    everything: `_seed_data` fires only into an empty directory, so the
    first edit -- or the first upload, or a stray `.DS_Store` -- makes the
    host copy the only copy for as long as the site lives (ADR-0008).
    Claiming `True` where that is not so invites a restore that quietly
    reinstalls the shipped version over content nobody can get back."""


@dataclass(frozen=True)
class SiteApp:
    """An installable unit of site functionality.

    Subclass it to report health or status: both methods return None by
    default, which podpack reports as "not reported" rather than as healthy.
    Absence of an answer is not an answer.
    """

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

    needs_secrets: frozenset[str] = frozenset()
    """Environment variables this app cannot work without.

    Declared here for the same reason `needs_tables` is: the author knows, and
    the site owner installing the app has no way to. Checked at boot with
    podpack's own and the site's, so installing an app that needs a credential
    stops the deployment rather than failing the first time somebody uses the
    feature::

        site_app = SiteApp(blueprint=bp, needs_secrets=frozenset({"MAPS_API_KEY"}))

    Checked *after* the apps are imported, which is the earliest moment their
    declarations exist -- podpack's own three are checked before anything reads
    them, which is earlier still. Both are long before the site serves.

    Only what the app genuinely cannot run without. A key that turns on an
    optional feature belongs in `[apps.<name>]` config with a sensible absence,
    not here: naming it here makes the whole site refuse to start.
    """

    defines_tables: frozenset[str] = frozenset()
    """Tables this app defines that attribution cannot see.

    Almost always empty, and deliberately so: what an app defines is read from
    SQLAlchemy's mapper registry, which is a fact rather than a declaration and
    cannot drift. This is for the one thing that registry does not know about
    -- a table with no mapped class, such as an association table built with a
    bare `db.Table`, or one constructed inside a dependency. flask-security's
    `roles_users` is both, which is how the gap was found::

        association = db.Table("thing_tags", db.Column(...), db.Column(...))
        site_app = SiteApp(blueprint=bp, defines_tables=frozenset({"thing_tags"}))

    Without it, an app in that position declares a need the dependency check
    cannot satisfy, and the site refuses to start over a table the app itself
    is sitting on.
    """

    needs_tables: frozenset[str] = frozenset()
    """Tables this app reads or writes but does not define.

    An app joining to `user` needs it as genuinely as podpack, which defines
    it, and several apps may need one table -- so this is a *need*, not a
    claim, and nothing here says anybody owns anything. What an app defines is
    read from the mapper registry instead, and needs nothing declared::

        site_app = SiteApp(blueprint=bp, needs_tables=frozenset({"user"}))

    Declaring one has two effects. It stops the unprefixed-name warning for
    that table, because a name you have said out loud is not the accident that
    warning is for. And it is checked at boot: a table nothing installed
    defines is a missing dependency, and the site refuses to start rather than
    serving until the first query fails.

    `db.metadata` is the one namespace podpack does not divide by app name,
    which is why any of this needs saying at all.
    """

    backs_up: "Backup | None" = None
    """What this app stores, for a site backing itself up.

    Declared here for the same reason `needs_tables` is: the author knows,
    and the site owner installing the app has no way to. Almost always
    unnecessary -- see `Backup`, which exists for what podpack cannot work
    out by looking::

        site_app = SiteApp(blueprint=bp, backs_up=Backup(data=False))

    `None` means the app has not said, and podpack reads that as *back
    everything up, and report that nobody vouched for it*. Backing up more
    than necessary costs disk; backing up less costs the data. It is not
    the same as `Backup(data=False)`, which is a claim somebody can be held
    to, and which podpack will contradict at boot if the directory says
    otherwise.

    Unlike every other declaration on this class, a wrong answer here is not
    checked by refusing to start. Nothing it describes is needed until a
    restore, and taking a working site down over a backup declaration would
    trade a real outage for a hypothetical one; so this one warns and
    reports, and `/_status` carries the answer for anybody who asks.
    """

    def healthz(self) -> "Health | None":
        """Override to say whether this app is working.

        Called in an app context on every `/healthz`, which the container
        healthcheck hits every ten seconds -- so keep it cheap. It may do
        I/O (pinging the store you depend on is the whole point), and
        podpack reports how long it took so a slow check is visible rather
        than mysterious. An exception here is caught and reported as an
        unhealthy app, never as a broken site.
        """
        return None

    def status(self) -> dict[str, Any] | None:
        """Override to add this app's own facts to `/_status`.

        Named `status` rather than `_status`: the endpoint's underscore marks
        podpack's reserved URL namespace, while a leading underscore on a
        method you are meant to override would say the opposite of what it
        means.

        Remember what `/_status` is for and who may read it -- report what
        an operator needs to diagnose a mount or a grant, not the contents
        of your tables.
        """
        return None

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
    admin: "Callable[[], bool] | None" = None
    """Whether the current request is an operator's.

    `create_app` fills this with `auth.is_admin` -- membership of the `admin`
    role -- unless the site passed its own, login being podpack's since
    ADR-0033. `None` remains "nobody qualifies", which is the right reading
    for a state `create_app` no longer produces but a directly-constructed
    state still can.
    """
    apps: dict[str, SiteApp] = field(default_factory=dict)
    nav: list[Section] = field(default_factory=list)

    defined_by: dict[str, str] = field(default_factory=dict)
    """Table name to the one app that defines it.

    One name and not a set, because SQLAlchemy enforces that already: a second
    app defining a table another has defined is a boot failure rather than a
    merge.

    Read from the mapper registry, not deduced from the table's name. The
    prefix convention keeps names from colliding; it is not a source of truth
    about who owns them. It is only a warning, so an app may ignore it;
    prefixes are not unique between apps called `note` and `notes`; and the
    tables this framework most needed to attribute are `user` and `role`,
    which are flask-security's names and prefixed by nothing at all.
    """

    needed_by: dict[str, set[str]] = field(default_factory=dict)
    """Table name to every app that needs it -- a set, not one name.

    `db.metadata` is the one namespace podpack does not divide by app, so this
    is the only place "who is involved with this table?" needs asking, and
    `/_status` reports it.

    A set because sharing is the ordinary case and sole ownership is not: an
    app that joins to `user` needs it as genuinely as the one that defines it.
    This was `table_owners`, a `dict[str, str]`, and the second app to declare
    a table replaced the first in it silently -- which nothing detected,
    because a single owner was an assumption rather than something anybody had
    checked.
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
    # Here rather than in create_app's opening lines because this is the first
    # moment the declarations exist: an app's requirements arrive with the app.
    # Still before a single request is served, and all of them together, so
    # installing three apps that each want a credential is one message.
    check_secrets(
        {
            secret: f"app {site_app.name!r}"
            for site_app in state.apps.values()
            for secret in sorted(site_app.needs_secrets)
        }
    )
    _check_table_dependencies(state)


def _check_table_dependencies(state: PodpackState) -> None:
    """Refuse to start when an app needs a table nothing installed defines.

    A dependency between apps, mediated by the schema rather than by imports:
    an app that joins to `notes` needs whatever app defines `notes` to be
    installed too, and the site's `apps` list is where that is decided. Until
    this check existed the site booted, served every page, and failed at the
    first query -- a stated requirement nobody was reading.

    Checked against what is *declared*, not against the database. Whether the
    table has actually been created is alembic's business and `/_status`'s;
    whether anything even claims to define it is answerable here, at boot,
    which is far earlier and far cheaper.

    A failure rather than a warning, matching what an unknown app in `apps`
    already does: a site missing a dependency cannot do what it was configured
    to do, and saying so quietly would leave it to be discovered by a visitor.
    """
    missing = {
        table: needers
        for table, needers in state.needed_by.items()
        if table not in state.defined_by
    }
    if not missing:
        return
    detail = "; ".join(
        f"{table!r}, needed by {', '.join(repr(n) for n in sorted(needers))}"
        for table, needers in sorted(missing.items())
    )
    raise RuntimeError(
        f"this site is missing a table that nothing installed defines: {detail}. "
        "Either add the app that defines it to `[site] apps`, or stop "
        "installing the app that needs it."
    )


def _install(app: Flask, state: PodpackState, module_name: str) -> SiteApp:
    site_app = _import_app(module_name, state.needed_by, state.defined_by)
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
    _check_backup_claim(site_app, data_dir)

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


def _check_backup_claim(site_app: SiteApp, data_dir: Path) -> None:
    """Say when an app's claim of statelessness is contradicted by its disk.

    Warned rather than raised, and this is the one declaration on `SiteApp`
    treated that way. A missing table breaks the site now; a wrong backup
    declaration breaks nothing until somebody tries to restore, so refusing
    to boot would trade a real outage for a hypothetical one. podpack says
    so at every boot and reports it on `/_status`, which is the same thing
    it does for unclaimed tables and unclaimed directories -- report, never
    tidy away.

    Note what is *not* checked: an app that declares nothing. Silence is a
    legitimate state, backed up in full, and warning about it at every boot
    would train the reader to skip these lines.
    """
    if site_app.backs_up is None or site_app.backs_up.data:
        return
    try:
        left = sorted(entry.name for entry in data_dir.iterdir())
    except OSError:
        # An unreadable data directory is a mount problem, and the mount
        # check on /_status reports it properly. Not this warning's business.
        return
    if left:
        logger.warning(
            "app %r says it stores nothing, but %s holds %s. A backup will "
            "skip it on the app's word: either the declaration is wrong, or "
            "those files are not wanted.",
            site_app.name,
            data_dir,
            ", ".join(left[:5]) + (" ..." if len(left) > 5 else ""),
        )


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
    installed_site_apps(names)


@dataclass(frozen=True)
class Installed:
    """What one traversal of a site's app list learned about it."""

    apps: dict[str, SiteApp]
    """Each installed app's declaration, keyed by the app's own name."""

    defined_by: dict[str, str]
    """Which app defines each table, read from the mapper registry."""


def installed_site_apps(names: Iterable[str]) -> Installed:
    """Import every named app and keep what it declares, with no Flask app.

    The same traversal `import_app_models` performs and the same contract
    check -- it *is* that traversal now -- differing only in keeping what it
    learned rather than discarding it. Every declaration on `SiteApp` is
    readable from an imported module, so this needs no application, no secret
    key and no working database URI, which is what lets `podpack backup plan`
    answer from the command line (ADR-0010, ADR-0031).

    Written this way round on purpose. ADR-0010 warns that a second traversal
    of the app list is another thing to keep in step with `install_apps`, and
    a third would have been worse: `import_app_models` was already building
    these objects and throwing them away, so returning them keeps the count
    where it was.
    """
    needed_by: dict[str, set[str]] = {}
    defined_by: dict[str, str] = {}
    apps: dict[str, SiteApp] = {}
    for name in names:
        site_app = _import_app(name, needed_by, defined_by)
        apps[site_app.name] = site_app
    return Installed(apps=apps, defined_by=defined_by)


def _import_app(
    module_name: str, needed_by: dict[str, set[str]], defined_by: dict[str, str]
) -> SiteApp:
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
        raise RuntimeError(_clashing_table(module_name, needed_by, exc)) from exc

    # Two different statements, kept apart. What the app *defines* comes from
    # the mapper registry and is a fact; what it *needs* is a declaration, and
    # includes everything it defines, because defining a table is the strongest
    # possible way of needing it.
    defines = _tables_declared_by(module_name) | site_app.defines_tables
    for table in sorted(defines):
        defined_by[table] = site_app.name

    for table in sorted(defines | site_app.needs_tables):
        needed_by.setdefault(table, set()).add(site_app.name)
        if table in site_app.needs_tables:
            # Declared on purpose, so there is nothing to warn about. Recording
            # it above is the point of saying so -- this is a declaration, not
            # a mute, and joining a set rather than replacing a name means a
            # second app declaring the same table adds to the record instead of
            # quietly erasing the first.
            continue
        if not table.startswith(site_app.name):
            # Warned here rather than checked at the clash, because the clash
            # happens on whichever site installs both apps -- by which time the
            # author who could fix it is not the person reading the error.
            logger.warning(
                "app %r declares the table %r, which its own name does not "
                "prefix. Table names are shared across every installed app, so "
                "a second app claiming %r will stop a site booting. Name it "
                "in the app's `needs_tables` if it is deliberate.",
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
    module_name: str, needed_by: dict[str, set[str]], exc: InvalidRequestError
) -> str:
    """Name every app in a table-name collision, where SQLAlchemy names none.

    The table is read out of SQLAlchemy's message because the import that would
    have told us aborted part way through. A miss is survivable -- the apps
    installed so far and what they declared is still more than the original
    error said -- so this reports what it has rather than insisting on a match.
    """
    def who(names: set[str]) -> str:
        return ", ".join(repr(name) for name in sorted(names))

    match = _ALREADY_DEFINED.search(str(exc))
    table = match.group(1) if match else None
    if table is not None and table in needed_by:
        clash = f"the table {table!r}, already needed by {who(needed_by[table])}"
    elif table is not None:
        clash = f"the table {table!r}, which is already on db.metadata"
    else:
        declared = ", ".join(f"{t} ({who(a)})" for t, a in sorted(needed_by.items()))
        clash = f"a table already on db.metadata; declared so far: {declared or '(none)'}"
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
