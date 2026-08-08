"""podpack: a site is a config file plus a list of installed apps.

The framework supplies the factory, the app registry, the template search order
and the migration wiring. A site supplies its own chrome, its own content and
its app list. Apps ship as separate packages and are installed by name.

The smallest possible site is therefore a TOML file with an empty app list, and
adding a feature to a running site is an edit to that file and a restart -- no
code change, no rebuild, and no change to the compose file.
"""

import os
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from jinja2 import ChoiceLoader, PackageLoader

from .config import app_config, installed_apps, load_host_config, require_env
from .nav import Section, sections
from .registry import PodpackState, SiteApp, install_apps
from .urls import absolute_url, base_url, check_base_url

__all__ = [
    "Section",
    "SiteApp",
    "absolute_url",
    "app_config",
    "base_url",
    "create_app",
    "db",
    "sections",
]

# Created unbound and attached to an app inside create_app(), so that several
# app instances -- one per test, say -- can coexist safely.
db = SQLAlchemy()

DEFAULT_DATA_ROOT = "/var/lib/holdenweb/apps"
DEFAULT_LOG_ROOT = "/var/log/holdenweb/apps"


def create_app(
    config_overrides=None,
    *,
    site_package=None,
    init=None,
    host_config=None,
    config_path=None,
    data_root=None,
    log_root=None,
):
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
    def _nav():
        """Put the assembled navigation in front of every template, so that an
        app's pages get the site's chrome without each view remembering to pass
        it."""
        return {"sections": app.extensions["podpack"].nav, "site": host_config["site"]}

    return app


def _configure(app, host_config):
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


def _add_template_fallback(app):
    """Search podpack's own templates last of all.

    Flask already searches the app's template folder before any blueprint's, so
    a *site* can override an app's template simply by shipping one at the same
    namespaced path. Appending podpack's loader after the whole dispatching
    loader completes the order:

        site templates  ->  app templates  ->  podpack defaults

    which means an app renders against sensible chrome on a site that has not
    written any of its own yet, and every layer above can still override it.
    """
    app.jinja_env.loader = ChoiceLoader(
        [app.jinja_env.loader, PackageLoader("podpack", "templates")]
    )
