# ADR-0015: PostgreSQL stays in a container

**Status:** Accepted

**Date:** 2026-08-08

## Context

Opalstack is where this suite deploys, and `98f5073` established that it takes
it as it stands: AlmaLinux 9 servers running rootless podman, an Nginx Proxy
Port application supplying `WEB_HOST_PORT` and the host roots. The same
investigation turned up that Opalstack also provides a managed PostgreSQL 17 —
the same major version `compose.yaml` runs as `docker.io/library/postgres:17`.

So the container was not buying a version the platform could not offer. It buys
a different thing, and the question was whether that thing is worth running a
database server ourselves.

## Decision

The suite runs its own `postgres:17`. Its data lives under
`${HOST_DATA_DIR}/postgres`, its settings come from `./config/postgresql.conf`
mounted read-only, and its roles are created once by `db-init/`.

The deciding property is *when the version moves*. A pinned image moves when
this deployment pulls a new one; the managed instance moves when the server
does, and it is shared with everything else on that server, so the timing is
someone else's. That is the same instinct as mounting `postgresql.conf`
read-only so `ALTER SYSTEM` fails by design: the configuration and now the
engine version belong to version control rather than to whoever last had a
session on the machine.

## Consequences

Backups, tuning and patching are the site's problem. `scripts/` contains
`prepare-host-dirs.sh` and `up.sh` and no `pg_dump` anywhere — running the
database has not yet been paid for in the one place it will be missed.

`postgres:17` is a floating tag, not a digest. A minor release arrives on any
pull that finds a newer one, so "the site decides" means the site decides when
it pulls, not that the bytes are frozen.

Two of the substrate's paid-for gotchas exist only because a database server is
in the stack: `init-storage` chowning the mounts for uid 999, and `PGDATA`
pointing one level inside its mount because a bind mount point's mode belongs to
the host. Both would go with the service.

Switching later is cheap, and that is what makes this reversible rather than
merely chosen. podpack learns about the database only from
`SQLALCHEMY_DATABASE_URI`, which lives in `secrets.env`
([ADR-0013](0013-environment-split-by-restore-semantics.md)). Dropping the
`postgres` service, its two `init-storage` mounts and the `db-init` mount and
repointing that one variable is the whole migration — no application code, no
change to the alembic history ([ADR-0009](0009-one-alembic-history.md)).

## Alternatives considered

**Use Opalstack's managed PostgreSQL 17.** Fewer moving parts and no data
directory to own, but the version and its upgrade schedule belong to a machine
shared with every other tenant of it.

**Install PostgreSQL on the host.** Gives back version control at the cost of
the property the whole substrate exists for: state and settings in the
container suite rather than in the machine's history.
