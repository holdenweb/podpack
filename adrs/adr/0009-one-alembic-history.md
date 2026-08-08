# ADR-0009: One alembic history, driven by the app list

**Status:** Accepted

**Date:** 2026-08-08

## Context

Before `624d9f5` the schema was whatever `create_all` produced at boot. Gunicorn
starts several workers at once and each ran it, so the losers crashed on tables a
sibling had just created — and nothing in that arrangement can change a column
anyway.

Alembic autogenerate compares the database against `db.metadata`, and a model
reaches that metadata only by being defined: subclassing `db.Model` registers the
table as an import side effect. So an app whose `models.py` nothing has imported
is invisible, and its tables silently never get created. Something therefore has
to import the installed apps' models before alembic runs, and the only thing that
knows which apps are installed is the site's config file.

Django's answer is a migration directory per app, which presumes apps are the
unit of deployment. A podpack site is one instance with one database
([ADR-0001](0001-one-site-per-instance.md)), and its apps ship together.

## Decision

The site has a single alembic history. `podpack.migrations.target_metadata()`
loads the host config, imports every installed app's models through
`registry.import_app_models()`, and returns `db.metadata`; `alembic/env.py` calls
it and knows nothing else about apps. Importing *is* registration — there is no
second registration call anyone can forget.

## Consequences

That metadata is built without constructing a Flask app, which is
[ADR-0010](0010-migrations-need-no-flask-app.md) and a decision in its own right.

The footgun, checked rather than assumed and rechecked while writing this:
autogenerate sees exactly the apps that are enabled, so running it against a
config with `apps = []` reports `Detected removed table 'notes'` and writes
`op.drop_table('notes')`. Autogenerate always against the full app list — a rule
nothing enforces.

A revision's directory no longer says which app it belongs to, so its message has
to, as in `alembic/versions/205fc0d0ce92_notes_app_initial_schema.py`. Nothing
checks that either.

Uninstalling an app leaves its tables in the database and its revisions in a
history the site still replays. There is no per-app schema to remove, and this
record does not solve it.

## Alternatives considered

Per-app histories — `version_locations` plus `branch_labels` — remain the answer
if the single history becomes painful. They cost multiple heads and merge
revisions to reason about, and buy nothing until a second site wants a different
subset of apps.

Constructing a Flask app in `env.py` to reach `db.metadata`, which is what
`holdenweb.com` does today: its `env.py` imports `holdenweb`, and that runs
`create_app()` as a side effect. Migrations then need a secret key to run.
