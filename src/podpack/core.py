"""The routes every site has, whatever apps it installs.

Deliberately three and no more. A home page so that a site with no apps still
serves something; a health endpoint because the container healthcheck needs one;
and a status report because the whole point of the container substrate is being
able to see, from outside, whether the mounts and grants are actually right.
"""

import os

import sqlalchemy as sa
from flask import Blueprint, current_app, jsonify, render_template
from sqlalchemy.exc import SQLAlchemyError

from . import db

core_blueprint = Blueprint("podpack", __name__)


@core_blueprint.route("/")
def home():
    return render_template("index.html", title=current_app.extensions["podpack"].host_config["site"]["name"])


@core_blueprint.route("/healthz")
def healthz():
    """Liveness *and* readiness: the site is no use without its database."""
    try:
        db.session.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify(status="unhealthy", database=str(exc)), 503
    return jsonify(status="ok", database="ok")


@core_blueprint.route("/_status")
def status():
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
