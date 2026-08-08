# ADR-0019: Drop uwsgi; gunicorn calls the factory directly

**Status:** Accepted

**Date:** 2026-08-08

## Context

`holdenweb.com/uwsgi.ini` carries an absolute host path on five of its ten
lines — `virtualenv`, `pidfile`, `wsgi-file` and `touch-reload` under
`/home/sholden/apps/second-alma/`, and `daemonize` under `/home/sholden/logs/`,
all rooted at `/home/sholden/apps/second-alma/`. The original brief asked for
those to be made relative to a configuration parameter, with a `deploy` utility
to establish it.

Containers run on Opalstack, so a podpack site deploys as the compose stack and
uwsgi is not in the picture. `Containerfile:98` already runs
`gunicorn ... 'podpack:create_app()'`; there is no WSGI entry file to point a
`wsgi-file` at.

The accounting matters more than the deletion. `wsgi.py`'s first line,
`from holdenweb import app, application`, is the *only* importer of either name
anywhere in that tree — the tests and `alembic/env.py` import `create_app` and
`db` instead. So `src/holdenweb/__init__.py:315-316` exists solely to feed
uwsgi, and because those two lines run at module level, importing `holdenweb`
builds a Flask app as a side effect. That is why `alembic/env.py:11` gets an
entire application from `from holdenweb import db`, and why it depends on
getting one: `create_app`'s `load_dotenv()` is what puts
`SQLALCHEMY_DATABASE_URI` in the environment for the lines below it. Nobody
chose that coupling; it arrived with the module-level `app`.

## Decision

podpack sites run under gunicorn calling the application factory directly. The
uwsgi path work and the `deploy` utility are cancelled, recorded in `c646dd9`.
When holdenweb.com is adapted, `uwsgi.ini`, `wsgi.py`, the `pyuwsgi>=2.0.30`
dependency and the module-level `app`/`application` go with them, which breaks
the import chain and gives that project the property
[ADR-0010](0010-migrations-need-no-flask-app.md) already guards here.

## Consequences

Most of uwsgi.ini relocates rather than vanishing. `workers = 2` becomes
`GUNICORN_WORKERS`, still 2 in `.env`. `master`, `daemonize` and `pidfile`
become podman plus `restart: unless-stopped` plus `loginctl enable-linger`.
`http-socket = 127.0.0.1:8456` becomes `WEB_BIND_ADDR:WEB_HOST_PORT`.

Two things do not survive. `touch-reload` has no container equivalent:
`./scripts/up.sh` is a rebuild, not a reload
([ADR-0017](0017-always-rebuild-and-stamp-the-commit.md)). And `threads = 2` has
nowhere to go — the CMD passes `--workers` and no worker class, so gunicorn's
default sync worker handles one request at a time and per-worker concurrency
drops.

This decision also outruns its execution. holdenweb.com is untouched, so the
files and the dependency are still there and the coupling still bites; this
record is why the removal is not a question when the adaptation reaches it.

## Alternatives considered

**Parameterise uwsgi.ini as asked.** Two deployment mechanisms to keep working,
and the module-level `app` survives — so migrations stay hostage to the factory
for the sake of a process manager nothing runs.

**Run uwsgi inside the container.** Then supervision exists twice, and podman's
restart policy and uwsgi's `master` disagree about who owns the process.
