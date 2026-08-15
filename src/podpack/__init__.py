"""podpack: a site is a config file plus a list of installed apps.

The framework supplies the factory, the app registry, the template search order
and the migration wiring. A site supplies its own chrome, its own content and
its app list. Apps ship as separate packages and are installed by name.

The smallest possible site is therefore a TOML file with an empty app list, and
adding a feature to a running site is an edit to that file and a restart -- no
code change, no rebuild, and no change to the compose file.
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from jinja2 import ChoiceLoader, PackageLoader

from .config import app_config, installed_apps, load_host_config, require_env
from .nav import Section, sections
from .registry import Health, PodpackState, SiteApp, install_apps
from .urls import absolute_url, base_url, check_base_url

__all__ = [
    "ADMIN_ROLE",
    "Health",
    "Section",
    "SiteApp",
    "absolute_url",
    "app_config",
    "base_url",
    "create_app",
    "db",
    "sections",
]

logger = logging.getLogger(__name__)

# Created unbound and attached to an app inside create_app(), so that several
# app instances -- one per test, say -- can coexist safely.
db = SQLAlchemy()

ADMIN_ROLE = "admin"
"""The role a site's `admin` predicate should ask about.

A name and nothing else. podpack ships no command to create it and no model
to hold it: flask-security already has `users create`, `roles create` and
`roles add`, and its versions validate the identity through the registration
form and resolve users the way the rest of it does. A framework command
would have been a worse copy of three that exist -- so what podpack
contributes here is one spelling, so that a site and its guard agree.

    flask --app <site> users create you@example.com --active
    flask --app <site> roles create admin
    flask --app <site> roles add you@example.com admin
"""

DEFAULT_DATA_ROOT = "/var/lib/holdenweb/apps"
DEFAULT_LOG_ROOT = "/var/log/holdenweb/apps"


def create_app(
    config_overrides: dict[str, Any] | None = None,
    *,
    site_package: str | None = None,
    init: Callable[[Flask], None] | None = None,
    admin: Callable[[], bool] | None = None,
    host_config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
    data_root: str | Path | None = None,
    log_root: str | Path | None = None,
) -> Flask:
    """Build a site.

    `site_package` names the package whose `templates/` and `static/` are the
    site's own; leaving it unset makes podpack itself the site, which is what
    the container lab does and what a brand-new site starts as.

    `init` is the site's own wiring: a `callable(app)` for the extensions and
    config that belong to the site rather than to any one feature -- mail, login,
    session policy. It is what an app's `SiteApp.init` is, one level up, and it
    exists because those things are not apps: `flask-mailman` and
    `flask-paranoid` register no blueprint at all, and `flask-security` brings
    its own, so dressing either as a `SiteApp` would mean inventing a blueprint
    to satisfy a contract built around having one.

    A site passes it from its own factory, which is also what gunicorn is
    pointed at::

        def create_app():
            return podpack.create_app(site_package="holdenweb", init=_wire)

    `admin` is a predicate answering "is this request an operator's?", and it
    guards `/_status`, which reports the site's database identity, its paths
    and its versions. podpack cannot answer that question itself: it has no
    login, because login is the site's to wire. Left unset, nobody qualifies
    and the endpoint reports nothing -- the safe default for a route that is
    otherwise reachable by anyone who can reach the site.

    `host_config` and the various roots exist so that tests can build a site
    without a mounted filesystem. In production every one of them is left unset
    and the values come from the config file and the environment.
    """
    if host_config is None:
        host_config = load_host_config(config_path)
    check_base_url(host_config)

    app = Flask(site_package or __name__)

    _configure(app, host_config)
    if config_overrides:
        app.config.update(config_overrides)

    app.extensions["podpack"] = PodpackState(
        admin=admin,
        host_config=host_config,
        data_root=Path(data_root or os.environ.get("PODPACK_DATA_ROOT", DEFAULT_DATA_ROOT)),
        log_root=Path(log_root or os.environ.get("PODPACK_LOG_ROOT", DEFAULT_LOG_ROOT)),
    )

    db.init_app(app)
    _add_template_fallback(app)

    from .core import core_blueprint, install_home_page

    app.register_blueprint(core_blueprint)

    # The site's own wiring runs before its apps, so an app's `init` can rely on
    # a service the site registered -- an app that sends mail should not have to
    # care whether mail happens to have been configured yet. The site's config is
    # already loaded and `app.extensions["podpack"]` already populated, so `init`
    # can read `app_config()` and the host config like anything else.
    if init is not None:
        init(app)

    install_apps(app, installed_apps(host_config))
    # After the apps, so anything that routes `/` keeps it; see install_home_page.
    install_home_page(app)

    @app.context_processor
    def _nav() -> dict[str, Any]:
        """Put the assembled navigation in front of every template, so that an
        app's pages get the site's chrome without each view remembering to pass
        it."""
        return {"sections": app.extensions["podpack"].nav, "site": host_config["site"]}

    _report_unreachable_status(app)

    return app


def _report_unreachable_status(app: Flask) -> None:
    """Say at boot when nobody can read `/_status`, because nothing else will.

    The endpoint refuses with 404 rather than 403 on purpose: that a site is a
    podpack site at all is not worth publishing. The cost is that a refusal and
    a route that does not exist look identical from outside, so an operator
    locked out by configuration has nothing to read. That happened -- a site
    with the predicate correctly wired, but with the `admin` role never created,
    answered 404 to every request including its owner's, and the only way to
    find out why was to query the database by hand.

    A warning, not an error. A site with no operator is perfectly legitimate --
    the container lab is one, and podpack has no login of its own to give it
    (ADR-0025) -- so this reports a fact and starts the site regardless.
    """
    if app.extensions["podpack"].admin is None:
        logger.warning(
            "no `admin` predicate: /_status will answer 404 to everyone. That is "
            "the safe default for a route reporting the database identity and "
            "every host path; pass create_app(admin=...) to name this site's "
            "operators."
        )
        return

    # podpack owns no role model and no user table -- the whole point of
    # ADR-0025 -- so there is nothing here it can query directly. What it can do
    # is ask the extension the site wired, if it wired that one. A site whose
    # predicate asks about something else entirely gets no warning, which is the
    # right way round: a false alarm about a role you deliberately do not use
    # would be worse than silence.
    security = app.extensions.get("security")
    datastore = getattr(security, "datastore", None)
    if datastore is None:
        return
    try:
        with app.app_context():
            missing = datastore.find_role(ADMIN_ROLE) is None
    except Exception as exc:  # noqa: BLE001 -- a diagnostic must not break a boot
        # Reached whenever the tables are not there yet, which is ordinary: the
        # `migrate` service creates them, and `create_app` runs in tests against
        # databases that have none. Not worth a warning; the real one is below.
        logger.debug("could not check for the %r role: %s", ADMIN_ROLE, exc)
        return
    if missing:
        logger.warning(
            "no %r role exists, so /_status will answer 404 to everyone -- "
            "including you. Create it and grant it:\n"
            "    flask --app %s roles create %s\n"
            "    flask --app %s roles add <email> %s",
            ADMIN_ROLE,
            app.import_name,
            ADMIN_ROLE,
            app.import_name,
            ADMIN_ROLE,
        )


def _configure(app: Flask, host_config: dict[str, Any]) -> None:
    """Apply the two configuration layers, secrets last.

    Connection details are a secret and come from the environment; how the pool
    is sized is not, and comes from the host config file.
    """
    database = host_config.get("database", {})
    limits = host_config.get("limits", {})

    app.config["SECRET_KEY"] = require_env("SECRET_KEY")
    app.config["SQLALCHEMY_DATABASE_URI"] = require_env("SQLALCHEMY_DATABASE_URI")
    app.config["SQLALCHEMY_ECHO"] = database.get("echo", False)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Only the options this site actually set are passed through. Supplying
    # defaults would be worse than it looks: SQLAlchemy rejects pool sizing
    # outright on dialects that do not pool that way -- SQLite's StaticPool for
    # one -- so a framework that always sent `pool_size` could never run on
    # SQLite, which is exactly what the tests and a first-run site want.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        key: database[key]
        for key in (
            "pool_size",
            "max_overflow",
            "pool_recycle",
            "pool_timeout",
            "pool_pre_ping",
        )
        if key in database
    }
    if "max_upload_bytes" in limits:
        app.config["MAX_CONTENT_LENGTH"] = limits["max_upload_bytes"]


def _add_template_fallback(app: Flask) -> None:
    """Search podpack's own templates last of all.

    Flask already searches the app's template folder before any blueprint's, so
    a *site* can override an app's template simply by shipping one at the same
    namespaced path. Appending podpack's loader after the whole dispatching
    loader completes the order:

        site templates  ->  app templates  ->  podpack defaults

    which means an app renders against sensible chrome on a site that has not
    written any of its own yet, and every layer above can still override it.
    """
    existing = app.jinja_env.loader
    fallback = PackageLoader("podpack", "templates")
    # Flask always installs a loader, but its type says otherwise and an app
    # built without one would otherwise get a ChoiceLoader with a None in it --
    # which fails later, while rendering, rather than here.
    app.jinja_env.loader = ChoiceLoader([existing, fallback]) if existing else fallback
