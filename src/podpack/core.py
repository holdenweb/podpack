"""The routes every site has, whatever apps it installs.

Two of them unconditionally: a health endpoint, because the container healthcheck
needs one, and a status report, because the whole point of the container
substrate is being able to see from outside whether the mounts and grants are
right. Both live under names a site is unlikely to want.

The third is `/`, and it is a *fallback* rather than a fixture -- see
`install_home_page`. A site's front page belongs to the site.
"""

import os
import time
from collections.abc import Mapping

import sqlalchemy as sa
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
)
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .paths import unclaimed
from .proxy import proxy_hops
from .registry import SiteApp

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


def _app_health() -> tuple[dict[str, dict[str, object]], bool]:
    """Ask every installed app how it is, and how long it took to answer.

    An app that raises is reported unhealthy rather than allowed to break the
    endpoint: a health check is the last thing that should be able to take a
    site down by failing.

    A failure is not fatal unless the app says so. `/healthz` gates the whole
    stack through the container healthcheck, so defaulting to fatal would let
    one broken feature stop a site from serving the rest.
    """
    reports: dict[str, dict[str, object]] = {}
    fatal = False
    for name, site_app in current_app.extensions["podpack"].apps.items():
        started = time.monotonic()
        try:
            health = site_app.healthz()
        except Exception as exc:  # noqa: BLE001 -- an app's bug is not the site's
            reports[name] = {"status": "unhealthy", "detail": f"{type(exc).__name__}: {exc}"}
            continue
        finally:
            elapsed = round((time.monotonic() - started) * 1000, 1)
        if health is None:
            continue          # not reported is not the same as healthy
        reports[name] = {
            "status": "ok" if health.ok else "unhealthy",
            "ms": elapsed,
        }
        if health.detail:
            reports[name]["detail"] = health.detail
        if not health.ok and health.fatal:
            fatal = True
            reports[name]["fatal"] = True
    return reports, fatal


@core_blueprint.route("/healthz")
def healthz() -> ResponseReturnValue:
    """Liveness *and* readiness: the site is no use without its database.

    Installed apps may add to that, and by default may only *add* to the
    report rather than to the verdict -- see `_app_health`.
    """
    body: dict[str, object] = {"status": "ok", "database": "ok"}
    healthy = True
    try:
        db.session.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        db.session.rollback()
        body["database"] = str(exc)
        healthy = False

    reports, fatal = _app_health()
    if reports:
        # Detail is an app's own words about its own failure, which may name
        # a host or a path. The container healthcheck reads the status code
        # and nothing else, so the public answer says only which app is
        # unwell; the sentence is in /_status, behind the guard.
        operator = _is_operator()
        body["apps"] = reports if operator else {
            name: {"status": report["status"]} for name, report in reports.items()
        }
    if not healthy or fatal:
        body["status"] = "unhealthy"
        return jsonify(**body), 503
    return jsonify(**body)


def _database_identity() -> dict[str, str]:
    """Which database, role and schema the site is actually connected as.

    Asking the server rather than parsing the URI is the point: it answers what
    the connection *became*, including the search_path the bootstrap set, which
    is what catches a wrong grant. But those three functions are
    PostgreSQL's, so on SQLite -- which is what a site uses before it has a
    database server -- the query fails and used to take the whole route with it.
    A diagnostic that only works once everything is right is no diagnostic.
    """
    try:
        row = db.session.execute(
            sa.text("SELECT current_database(), current_user, current_schema()")
        ).one()
    except SQLAlchemyError:
        db.session.rollback()
        url = db.engine.url
        return {
            "database": url.database or "(none)",
            "database_user": url.username or "(not applicable)",
            "database_schema": f"(not reported by {url.get_backend_name()})",
        }
    return {"database": row[0], "database_user": row[1], "database_schema": row[2]}


# Alembic's bookkeeping, and nobody's app. Not configured anywhere in this
# substrate, so the default name is the name; a site that sets `version_table`
# in its env.py will see that table reported as unclaimed, which is a fair
# description of what podpack then knows about it.
ALEMBIC_VERSION_TABLE = "alembic_version"


def _unclaimed_tables(needed_by: Mapping[str, set[str]]) -> list[str] | str:
    """Tables in the database that no installed app, and not podpack, needs.

    The same question `unclaimed()` asks of the data and log roots, asked of the
    one namespace that is shared rather than divided by app name -- and asked of
    the *database* rather than of `db.metadata`, for the same reason the roots
    are read from disk: what a site declares and what it has are different
    things, and the gap is the entire point. A table outlives the app removed
    from `apps`, exactly as its data directory does.

    Reported, never dropped. Removing a table because a config line changed
    would destroy the data an uninstall deliberately preserves.
    """
    try:
        present = set(sa.inspect(db.engine).get_table_names())
    except SQLAlchemyError as exc:
        # Same discipline as _database_identity: a diagnostic that only works
        # once everything is right is no diagnostic.
        db.session.rollback()
        return f"(not reported: {type(exc).__name__})"
    return sorted(present - set(needed_by) - {ALEMBIC_VERSION_TABLE})


def _reported(site_app: SiteApp) -> dict[str, object]:
    """An app's own contribution, ready to merge, or why it could not make one.

    Returns an empty mapping when the app says nothing, so `reported` is
    absent rather than null -- and a raising app is reported rather than
    allowed to break the one endpoint an operator reaches for when something
    is already wrong.
    """
    try:
        reported = site_app.status()
    except Exception as exc:  # noqa: BLE001 -- a diagnostic must not need diagnosing
        return {"reported": {"error": f"{type(exc).__name__}: {exc}"}}
    return {} if reported is None else {"reported": reported}


def _proxy_report() -> dict[str, object]:
    """What the proxy said, and what this site concluded from it.

    Both halves, because either alone is ambiguous: a site reporting `http`
    might be behind a proxy that sends no header, or in front of one it has
    not been told to trust, and the remedy differs. This is also the only way
    to settle the question on a running host without sending a password-reset
    mail to somebody and reading the link out of it.
    """
    return {
        "hops_trusted": proxy_hops(),
        "forwarded_proto": request.headers.get("X-Forwarded-Proto", "(not sent)"),
        "scheme": request.scheme,
        "host": request.host,
    }


def _is_operator() -> bool:
    """Whether this request may read the site's own configuration.

    `auth.is_admin` unless the site replaced it (ADR-0033). An exception in it
    counts as a refusal, and so does `None`: a guard that fails open is not a
    guard, and `create_app` can no longer leave this unset in any case.
    """
    admin = current_app.extensions["podpack"].admin
    if admin is None:
        return False
    try:
        return bool(admin())
    except Exception:  # noqa: BLE001 -- a broken guard denies, it does not admit
        return False


@core_blueprint.route("/_status")
def status() -> ResponseReturnValue:
    """Report where every piece of this site's state actually lives.

    If a bind mount or a grant is wrong, this route says so -- which is why the
    per-app directories are listed with their writability rather than merely
    named. It reports the app list too, so that "did my config edit take
    effect?" is answerable without reading the container's environment.
    """
    if not _is_operator():
        # 404 rather than 403: this route's existence, and the fact that a
        # site is a podpack site at all, is itself information an operator
        # has no reason to publish.
        abort(404)
    state = current_app.extensions["podpack"]
    return jsonify(
        **_database_identity(),
        site=state.host_config["site"],
        config_source=os.environ.get("PODPACK_CONFIG", "(default)"),
        # The scheme and host this site believes it serves on, which is what
        # every `_external=True` URL is built from -- a password-reset mail
        # among them. See podpack.proxy for why it is a host's decision.
        proxy=_proxy_report(),
        # Baked in at build time. The question this answers is "is the container
        # running the code I am looking at?", which a timestamp only approximates
        # -- editing framework source needs a rebuild, not a restart, and the
        # symptom of forgetting is a site that behaves like the previous commit.
        # A `-dirty` suffix means the image was built from an uncommitted tree.
        build_commit=os.environ.get("PODPACK_BUILD_COMMIT", "unknown"),
        data_root=str(state.data_root),
        log_root=str(state.log_root),
        # What is on disk that no installed app answers for -- normally empty.
        # An app removed from `apps` keeps its data, deliberately, so this is
        # how that data stays visible rather than merely still being there.
        unclaimed={
            "data": unclaimed(state.data_root, state.apps),
            "logs": unclaimed(state.log_root, state.apps),
            "tables": _unclaimed_tables(state.needed_by),
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
                # The one namespace apps share, so the only one worth
                # reporting rather than deriving from the app's name. Split
                # because they answer different questions: what this app would
                # take with it if uninstalled, and what it would break without.
                "defines_tables": sorted(
                    table for table, definer in state.defined_by.items() if definer == name
                ),
                "needs_tables": sorted(
                    table
                    for table, needers in state.needed_by.items()
                    if name in needers and state.defined_by.get(table) != name
                ),
                "stored_files": sorted(
                    p.name for p in (state.data_root / name).iterdir() if p.is_file()
                ),
                # What a backup of this site would do with the app, and
                # whether anybody said so. `null` is not "nothing to keep":
                # it is nobody having answered, which a backup resolves by
                # keeping everything. Reported beside `stored_files` because
                # the two together are the whole question -- an app claiming
                # to store nothing while listing files is the contradiction
                # podpack warns about at boot.
                "backs_up": (
                    None
                    if site_app.backs_up is None
                    else {
                        "data": site_app.backs_up.data,
                        "excludes": sorted(site_app.backs_up.excludes),
                        "extra": list(site_app.backs_up.extra),
                        "reseedable": site_app.backs_up.reseedable,
                    }
                ),
                # Whatever the app itself chooses to report. Absent when it
                # says nothing, so an app with nothing to say is
                # distinguishable from one that reported an empty answer.
                **_reported(site_app),
            }
            for name, site_app in state.apps.items()
        },
    )
