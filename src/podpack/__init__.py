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
from jinja2 import ChoiceLoader, PackageLoader

from . import auth
from .auth import ADMIN_ROLE, User, is_admin
from .config import (
    app_config,
    check_secrets,
    framework_secrets,
    installed_apps,
    load_host_config,
    require_env,
)
from .database import db
from .nav import Section, sections
from .proxy import proxy_hops, trust_proxy
from .registry import Backup, Health, PodpackState, SiteApp, install_apps
from .urls import absolute_url, base_url, check_base_url

__all__ = [
    "ADMIN_ROLE",
    "Backup",
    "Health",
    "Section",
    "SiteApp",
    "User",
    "absolute_url",
    "app_config",
    "base_url",
    "create_app",
    "db",
    "is_admin",
    "proxy_hops",
    "sections",
]

logger = logging.getLogger(__name__)

# Created unbound and attached to an app inside create_app(), so that several
# app instances -- one per test, say -- can coexist safely.

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
    and its versions. Left unset it is `auth.is_admin` -- membership of the
    `admin` role -- because login is podpack's since ADR-0033. Pass one only
    if this site's idea of an operator is not that.

    `host_config` and the various roots exist so that tests can build a site
    without a mounted filesystem. In production every one of them is left unset
    and the values come from the config file and the environment.
    """
    if host_config is None:
        host_config = load_host_config(config_path)
    check_base_url(host_config)
    # Before anything reads one, so that a misconfigured deployment says which
    # secrets it is short of -- all of them, in one message -- rather than
    # failing later and more obscurely on whichever is read first.
    check_secrets(framework_secrets(host_config))
    # Read here, with the other boot checks, so an unreadable value is one
    # clear message rather than a traceback out of the middleware stack on
    # whichever request happens to arrive first.
    hops = proxy_hops()

    app = Flask(site_package or __name__)
    # Before anything else touches wsgi_app, so the forwarded headers are
    # resolved before any other middleware reads the environ.
    trust_proxy(app, hops)

    _configure(app, host_config)
    if config_overrides:
        app.config.update(config_overrides)

    app.extensions["podpack"] = PodpackState(
        # A site may still pass its own predicate -- a test does, and a site
        # with an unusual idea of who counts as an operator may -- but it no
        # longer has to, and leaving it unset no longer means nobody qualifies.
        admin=admin if admin is not None else auth.is_admin,
        host_config=host_config,
        data_root=Path(data_root or os.environ.get("PODPACK_DATA_ROOT", DEFAULT_DATA_ROOT)),
        log_root=Path(log_root or os.environ.get("PODPACK_LOG_ROOT", DEFAULT_LOG_ROOT)),
    )
    # podpack is not an installed app, so attribution by defining module never
    # reaches its own tables: without this they would be reported as needed by
    # nobody, and every app declaring `needs_tables={"user"}` would fail the
    # dependency check against a framework that plainly does define it.
    # `roles_users` is the sharper case -- flask-security builds it inside
    # itself, so no mapper attributes it to anything at all.
    state = app.extensions["podpack"]
    for table in auth.NEEDED_TABLES:
        state.defined_by[table] = "podpack"
        state.needed_by.setdefault(table, set()).add("podpack")

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

    # After the site's `init`, which is where a site configures flask-security
    # -- its message strings, its templates, whether registration is open --
    # and where it names a mail_util_cls if it sends its own mail.
    auth.install(app, mail_util_cls=app.config.get("PODPACK_MAIL_UTIL_CLS"))

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

    A warning, not an error. A database with no `admin` role yet is the
    ordinary state of a site on its first run, and it should start and serve so
    that somebody can go and create one.

    The other half of this check used to be "no `admin` predicate was passed",
    which is gone: since ADR-0033 login is podpack's, `create_app` falls back
    to `auth.is_admin`, and there is no longer a way to end up with a site
    whose operator question nobody can answer. That warning fired for ever on
    two sites where the condition was permanent and correct -- exactly the
    noise this one exists to avoid becoming.
    """
    datastore = app.extensions["security"].datastore
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
    # Required since login became core (ADR-0033), and required rather than
    # defaulted for the reason a shared default would be worse than useless:
    # flask-security's "salt" is the HMAC key its password hashes are keyed on,
    # so one baked into the framework would make every podpack site's hashes
    # verifiable against every other's.
    #
    # Failing here, at boot, is the whole point. Without it the site starts
    # perfectly and dies later inside `flask users create` or a registration,
    # with flask-security's own message naming a setting nobody has heard of --
    # which is exactly how this was found.
    app.config["SECURITY_PASSWORD_SALT"] = require_env("SECURITY_PASSWORD_SALT")
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
