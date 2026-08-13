# ADR-0029: PostgreSQL is required; only MongoDB is optional

**Status:** Accepted — amends [ADR-0028](0028-core-services-are-overlays-the-site-chooses.md)

**Date:** 2026-08-13

## Context

ADR-0028 made every backing service a compose overlay a site chooses, and
made no distinction between them: `postgres` and `mongodb` were both entries
in the catalogue, both optional, and `podpack substrate init --services
mongodb` duly produced a site running MongoDB and no SQL server.

That site cannot start. `create_app` calls
`require_env("SQLALCHEMY_DATABASE_URI")` unconditionally before it builds
anything, and the only place that URI is supplied is postgres's own secrets
fragment — so the site it produced had no SQL URI anywhere, and would have
failed at boot with a message about a missing environment variable rather
than anything resembling its actual cause. The defect shipped because the
site was verified by running `init` and reading the result, and never
started.

The same conclusion arrives from the site's own features rather than the
framework's: flask-security's `user`, `role` and `roles_users` tables are
SQL, so login is unavailable on a site with no SQL server. ADR-0028 had
already written down that "SQL is the one store an app may assume" — it
simply failed to draw the consequence that a site therefore cannot decline
the thing that provides it.

## Decision

A `CoreService` declares whether it is `optional`. PostgreSQL is not:
`services.required()` always contains it, `normalise()` adds it to whatever
a site declares, and `compose_file_line()` always names its overlay. `init
--services` and `services --add` accept only optional services, and naming
a required one says so rather than pretending to act.

MongoDB is the only optional service today, and the catalogue is the place a
third would state which it is.

**What stays optional is the container, not the database.** Dropping
`compose.postgres.yaml` from `COMPOSE_FILE` by hand and pointing
`SQLALCHEMY_DATABASE_URI` at a managed PostgreSQL remains supported, and is
the arrangement ADR-0015 anticipated for Opalstack. podpack will not do it
for you — the same rule under which it removes no other service — and the
site is then responsible for a URI that reaches a server podpack knows
nothing about.

## Consequences

`--services mongodb` now yields a site running both, which is what the
person typing it meant. A site's recorded service list means "what this site
*chose*", and choosing is only meaningful where a choice exists — so the
list is normalised on read as well as on write, and a state file written
before a service became mandatory still describes a site that runs it.

The cost is a real asymmetry in a design whose whole argument was that
postgres should stop being special. It is special, and the honest reason is
not that SQL is better but that podpack's own core — `db`, one alembic
history, the login tables a site inherits — is written in it. A framework
that wanted no required store would have to make `db` optional too, which is
a much larger change than this one and buys nothing anybody has asked for.

The overlay stays a separate file even though it is always named. That
costs a file that could have been inlined, and keeps the managed-PostgreSQL
escape hatch to one line of `COMPOSE_FILE` rather than an edit to a managed
compose file that every future upgrade would conflict with.

## Alternatives considered

- **Leave both optional and require SQL only when something needs it.** That
  means making `db` lazy, `require_env` conditional, and the alembic gate
  skippable — a large change to the framework's spine to support a site
  nobody has asked for.
- **Keep both optional and simply document that a site needs SQL.** Prose
  cannot stop `--services mongodb` producing a site that will not boot, and
  the failure surfaces as a missing environment variable rather than as the
  decision that caused it.
- **Move postgres back into the base `compose.yaml`.** Simplest to read, and
  it would weld the container back in — losing the managed-PostgreSQL route
  ADR-0015 wants, which is the one case where a site legitimately runs no
  postgres container at all.
