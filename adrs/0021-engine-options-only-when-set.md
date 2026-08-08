# ADR-0021: Pass SQLAlchemy engine options only when the site sets them

**Status:** Accepted

**Date:** 2026-08-08

## Context

How the connection pool is sized varies by host, so it belongs in the mounted
config file rather than the image
([ADR-0018](0018-config-in-files-secrets-in-the-environment.md)). The
`[database]` section of `config/app.toml` carries it: `pool_size = 5`,
`max_overflow = 5`, `pool_pre_ping = true`. The obvious way to read such a
section is `database.get("pool_size", 5)` — every key always present, the
framework choosing reasonable numbers for a site that says nothing.

That cannot work. Flask-SQLAlchemy substitutes `StaticPool` for an in-memory
SQLite database, and `StaticPool` has no size, so the option arrives at
`create_engine` as a keyword nothing will take:

```
TypeError: Invalid argument(s) 'pool_size' sent to create_engine(),
using configuration SQLiteDialect_pysqlite/StaticPool/Engine
```

Verified by calling `create_app` with `{"database": {"pool_size": 5}}` against
`sqlite:///:memory:`: `create_app` itself raises. That URI is what
`tests/conftest.py:11` hands every test, so a framework that always sent pool
sizing would fail the whole suite, and would be unable to run on the one
database available to a site with no server yet.

## Decision

`_configure()` in `src/podpack/__init__.py` builds `SQLALCHEMY_ENGINE_OPTIONS`
as a comprehension over five known keys — `pool_size`, `max_overflow`,
`pool_recycle`, `pool_timeout`, `pool_pre_ping` — keeping only those the host
config actually names, and defaulting none of them. `MAX_CONTENT_LENGTH` on the
following line follows the same rule. This arrived with the framework in
`27eb69d` and is gotcha 11 in `claude.md`.

## Consequences

podpack runs on SQLite, which is what the tests and a first-run site need, and
on PostgreSQL with whatever the mounted file says.

A site gets no pooling advice at all. Production tuning starts from an empty
dict, and an option left out is silently the driver's default rather than a
considered one — from inside the application the two are indistinguishable. The
five-key tuple is also a whitelist: an engine option podpack has not heard of is
dropped without a word.

`echo` is the exception. It is not an engine keyword, and `SQLALCHEMY_ECHO` is
set unconditionally from `database.get("echo", False)`, so one `[database]` key
does carry a framework default.

No test names this behaviour. The protection is incidental — reinstating a
default breaks every test that builds an app, loudly, but nothing explains why.

## Alternatives considered

**Defaults plus a dialect check**: read the URI's scheme and skip pool options
for SQLite. It puts dialect knowledge in the framework to work around a driver
detail, and wants extending for the next dialect that pools differently.

**Pass the whole `[database]` table through untouched.** No whitelist to
maintain, at the price of a mistyped key becoming a `TypeError` out of
`create_engine` instead of a setting that is simply absent.
