# ADR-0011: Revisions are authored on the host, applied in the container

**Status:** Accepted

**Date:** 2026-08-08

## Context

Alembic has two verbs and the README treated them as one job. `revision
--autogenerate` was documented as `podman compose run --rm migrate alembic
revision --autogenerate -m "what changed"`, sitting directly above `upgrade
head` in the same code block, as though authoring and applying were the same
work done in the same place.

That command could never have worked, and reading it never showed that.
`/app/alembic/versions` is copied into the image as root while the runtime stage
drops to `USER 10001:10001`, so alembic connects, reflects the database,
compares it against `db.metadata` and then dies on the final write with
`PermissionError` — the entire cost of autogeneration and none of its result.
Permission would not have saved it either: `--rm` deletes the container, and
with it a file whose only purpose is to be committed. `cdbae5a` corrected the
documentation and recorded the general lesson: a documented command nobody has
run is an untested assertion.

## Decision

Generating a revision happens on the host, with `PODPACK_CONFIG=config/app.toml`
and `SQLALCHEMY_DATABASE_URI` pointed at the cluster's loopback-published port,
because the result is a file that belongs in the repository. Applying one
happens in the container: the `migrate` service in `compose.yaml` runs
`command: ["alembic", "upgrade", "head"]`, `restart: "no"`, from the same image
as `web`, gated behind `postgres` being healthy and gating `web` with
`service_completed_successfully`. The image's code being read-only to the
process running it is the right arrangement, not an obstacle to work around.

## Consequences

A failed migration now stops the site coming up at all, rather than letting
`web` serve against a half-migrated schema. That also retired the `create_all`
race, where gunicorn started several workers at once and the losers crashed on
tables a sibling had just created.

The cost lands on the host, which must now be a full development install: the
project synced, every installed app importable, and a database reachable, since
autogenerate compares against a live one. `POSTGRES_HOST_PORT=5433` stays
published on loopback for a task that has nothing to do with serving the site.

The host's app list, not the deployed one, therefore decides what a revision
contains. Run autogenerate with an app missing from `apps` and alembic will
propose dropping its tables, because from where it is standing nothing claims
them — the footgun that follows from the list being configuration
([ADR-0004](0004-app-list-is-configuration.md)) and from there being one history
for one site ([ADR-0001](0001-one-site-per-instance.md)).

## Alternatives considered

**Bind-mount `alembic/versions` into the container for authoring.** Grants the
write and lands the file on the host, but makes the running site's code writable
by the process running it, to save installing a tool the host already has.

**`db.create_all()` at startup.** No revisions to author, and no history: it
cannot express a column rename or a backfill, and it is what the ordering gate
replaced.

**Migrate from `web`'s entrypoint.** Removes the extra service and restores the
race in a new place, since every worker would run it, with nothing to gate on.
