"""The routes every site has, whatever apps it installs.

Two of them unconditionally: a health endpoint, because the container healthcheck
needs one, and a status report, because the whole point of the container
substrate is being able to see from outside whether the mounts and grants are
right. Both live under names a site is unlikely to want.

The third is `/`, and it is a *fallback* rather than a fixture -- see
`install_home_page`. A site's front page belongs to the site.
"""

import os

import sqlalchemy as sa
from flask import Blueprint, Flask, current_app, jsonify, render_template
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .paths import unclaimed

core_blueprint = Blueprint("podpack", __name__)


def install_home_page(app: Flask) -> None:
    """Serve a default front page, but only if nothing else wants `/`.

    A site with no apps is a perfectly valid site and should show something
    rather than 404, which is why this exists at all. But `/` is the most
    valuable address a site has, and the framework holding it permanently would
    contradict the rule that the shape of the address space belongs to the site
    (ADR-0006). Registering it unconditionally did exactly that: an app that also
    routed `/` lost silently, because both rules existed and Werkzeug matched
    whichever was added first, which was always podpack's.

    Called after the apps are installed, so "wants it" means any rule they added.
    A site registering its own `/` after `create_app` returns is too late for
    this check -- its front page belongs in an app, which is where its templates
    and its nav entry already live.
    """
    if any(str(rule.rule) == "/" for rule in app.url_map.iter_rules()):
        return

    @app.route("/")
    def home() -> ResponseReturnValue:
        state = app.extensions["podpack"]
        return render_template("index.html", title=state.host_config["site"]["name"])


@core_blueprint.route("/healthz")
def healthz() -> ResponseReturnValue:
    """Liveness *and* readiness: the site is no use without its database."""
    try:
        db.session.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify(status="unhealthy", database=str(exc)), 503
    return jsonify(status="ok", database="ok")


@core_blueprint.route("/_status")
def status() -> ResponseReturnValue:
    """Report where every piece of this site's state actually lives.

    If a bind mount or a grant is wrong, this route says so -- which is why the
    per-app directories are listed with their writability rather than merely
    named. It reports the app list too, so that "did my config edit take
    effect?" is answerable without reading the container's environment.
    """
    state = current_app.extensions["podpack"]
    row = db.session.execute(
        sa.text("SELECT current_database(), current_user, current_schema()")
    ).one()
    return jsonify(
        site=state.host_config["site"],
        config_source=os.environ.get("PODPACK_CONFIG", "(default)"),
        # Baked in at build time. The question this answers is "is the container
        # running the code I am looking at?", which a timestamp only approximates
        # -- editing framework source needs a rebuild, not a restart, and the
        # symptom of forgetting is a site that behaves like the previous commit.
        # A `-dirty` suffix means the image was built from an uncommitted tree.
        build_commit=os.environ.get("PODPACK_BUILD_COMMIT", "unknown"),
        database=row[0],
        database_user=row[1],
        database_schema=row[2],
        data_root=str(state.data_root),
        log_root=str(state.log_root),
        # What is on disk that no installed app answers for -- normally empty.
        # An app removed from `apps` keeps its data, deliberately, so this is
        # how that data stays visible rather than merely still being there.
        unclaimed={
            "data": unclaimed(state.data_root, state.apps),
            "logs": unclaimed(state.log_root, state.apps),
        },
        apps={
            name: {
                # The import name in `apps`, which is not always the app's own
                # name -- and it is the app's name that keys `[site.mounts]`,
                # `[apps.<name>]` and the directories below.
                "installed_from": state.installed_from.get(name),
                "url_prefix": site_app.url_prefix,
                "data_dir": str(state.data_root / name),
                "data_dir_writable": os.access(state.data_root / name, os.W_OK),
                "log_dir": str(state.log_root / name),
                "log_dir_writable": os.access(state.log_root / name, os.W_OK),
                "stored_files": sorted(
                    p.name for p in (state.data_root / name).iterdir() if p.is_file()
                ),
            }
            for name, site_app in state.apps.items()
        },
    )
