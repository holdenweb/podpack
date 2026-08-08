# ADR-0018: Non-secret host settings in files, secrets in the environment

**Status:** Accepted

**Date:** 2026-08-08

## Context

Nothing host-specific may live inside an image, so every setting that varies by
host has to arrive from outside it. Two kinds of value do, and they want
opposite handling.

The site that prompted this framework treats them alike and reads the lot from
the process environment. `holdenweb.com/src/holdenweb/__init__.py:250` is
`app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY")`, and a dozen lines
around it follow the same pattern: a forgotten variable is `None`, the app boots
happily, and the mistake surfaces later as behaviour nobody can account for.
That style also makes a deployment unreadable. The installed-app list is most of
what a site *is* ([ADR-0004](0004-app-list-is-configuration.md)), and none of it
can be diffed, reviewed or committed while it lives in a process environment.

The database has the same problem from the other end. Left to itself PostgreSQL
keeps its settings in the data directory, where `ALTER SYSTEM` rewrites them and
the record of who changed what is a shell session that ended months ago.

## Decision

Non-secret per-host settings live in `config/app.toml`, bind-mounted read-only
at `/etc/holdenweb/app.toml` and read by `load_host_config` in
`src/podpack/config.py`. Secrets arrive through the environment: `require_env`
fetches `SECRET_KEY` and `SQLALCHEMY_DATABASE_URI`, and those two are the whole
of it (`src/podpack/__init__.py:101-102`). Neither loader falls back. A missing
file means a wrong bind mount and a missing variable means a broken deployment,
so both raise and the site does not start. `config/postgresql.conf` and its two
companions, all introduced with the substrate in 7c9adb5, are mounted `:ro` and
selected with `-c config_file=`, which makes `ALTER SYSTEM` fail by design.

## Consequences

Reviewing a deployment is now reading a tracked file. Tuning the pool or the
database is an edit and a restart, with no rebuild.

The costs are real. The connection URI carries the password, so the *whole* URI
is a secret and the host, port and database name are dragged into the
environment with it, unreviewable despite not being secrets themselves. The
site's name is written twice — `SITE_NAME` in `.env` and `name` in
`config/app.toml` — because compose cannot read TOML, and the two are kept in
step by hand. Only `config/` is tracked; `.env` and `secrets.env` are
gitignored, so half the deployment stays off the record
([ADR-0013](0013-environment-split-by-restore-semantics.md)). And because
PostgreSQL ignores initdb's generated file entirely once configured from
outside the data directory, every setting must be named in ours or left at the
built-in default.

Refusing to boot means a wrong mount takes the site down rather than degrading
it. Tests avoid the filesystem by passing `host_config=` to `create_app`.

## Alternatives considered

**Everything in the environment**, as holdenweb.com does. Nothing to mount, but
`os.environ.get` returns `None` for a typo and the app list becomes invisible.

**Everything in the file.** One place to look, at the price of writing
credentials onto disk inside a bind mount and into version control's path.

**Defaults when a source is missing.** Cheaper to run and much more expensive to
debug: the failure moves from boot to whenever the wrong value first matters.
