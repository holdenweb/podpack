# ADR-0010: The migration environment builds no Flask app

**Status:** Accepted

**Date:** 2026-08-08

## Context

Something has to import the installed apps' models before alembic can compare
the database against `db.metadata` ([ADR-0009](0009-one-alembic-history.md)).
The obvious candidate is the application factory, which already walks the app
list, and taking metadata off a live application is what most Flask projects do.

`create_app` in `src/podpack/__init__.py` will not run without three things. It
calls `load_host_config`, which refuses a missing file rather than defaulting,
and then `require_env` for `SECRET_KEY` and `SQLALCHEMY_DATABASE_URI` (lines
101-102). Route migrations through it and each of those becomes a precondition
for changing the schema — a typo in an app's `init`, or a secret not yet in the
environment on a new host, would take the migrations down along with the site.
A migration is often how you dig out.

`holdenweb.com` shows what the coupling costs once it sets in.
`src/holdenweb/__init__.py:315-316` builds `app = create_app()` at module level
to feed uwsgi, so `alembic/env.py:11` reads `from holdenweb import db` and gets
an entire application as an import side effect — and relies on it, since
`create_app`'s `load_dotenv()` is what puts `SQLALCHEMY_DATABASE_URI` in the
environment for the lines below. Nobody chose that; it arrived with the
module-level `app`.

## Decision

`podpack.migrations.target_metadata()`, added in `624d9f5`, reads the host
config, calls `registry.import_app_models()` — which imports each named app and
its `models` module and nothing else — and returns `db.metadata`. No Flask app
is constructed and no application context exists.
`test_migration_metadata_needs_no_application` in `tests/test_registry.py`
deletes `SECRET_KEY` and `SQLALCHEMY_DATABASE_URI` before asking for the
metadata. Since `create_app` without them raises `required environment variable
SECRET_KEY is not set`, anything that starts building an app fails in that test
rather than on a host.

## Consequences

`import_app_models` is a second traversal of the app list beside `install_apps`,
and the two have to stay in step. A table exists as far as alembic is concerned
only if defining it is an import side effect of `<app>.models`; an app creating
tables from its `SiteApp.init`, or conditionally on its `[apps.<name>]` config,
is invisible to autogenerate and nothing will say so.

Nothing at metadata time has `current_app`, or the per-app directories
`prepare()` makes ([ADR-0007](0007-per-app-data-and-log-directories.md)):
`import_app_models` cannot make them, having no app to make them for.

`alembic/env.py` therefore reads `SQLALCHEMY_DATABASE_URI` from the environment
itself. A comment says why it must be the same variable the site uses, and
nothing enforces it — a site that configured its database anywhere else would
have alembic quietly pointing elsewhere.

What it buys shows in the documented procedure: authoring a revision on the host
needs `PODPACK_CONFIG` and a database URI, and no secret. `alembic upgrade head
--sql` with `SECRET_KEY` unset emits the notes schema.

## Alternatives considered

**Build the app in `env.py` and take the metadata from it.** holdenweb.com's
arrangement, and what happens if nobody decides. It works right up until the
factory does not.

**Hardcode the model imports in `alembic/env.py`.** No Flask app either, but a
second list of apps to drift from `[site] apps`, and the drift is silent: an app
nobody imported has no tables and no error.
