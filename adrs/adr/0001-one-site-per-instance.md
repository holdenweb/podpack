# ADR-0001: One site per running instance

**Status:** Accepted

**Date:** 2026-08-08

## Context

podpack grew out of the need to stand up `holdenweb.com` and then do it again
without starting over. The question that hung over the early design was whether
"again" meant a second deployment or a second site inside the same process.
Nothing in the code had answered it, and every simplification already made
quietly assumed one answer.

`db` is a single module-level `SQLAlchemy()` in `src/podpack/__init__.py`, so
there is one `db.metadata`. `alembic/versions/` holds one linear history.
`installed_apps()` in `config.py` reads one `[site] apps` list out of one
host-mounted TOML. Serving several domains from one process turns each of those
into a registry keyed by hostname: metadata per tenant, a migration story per
tenant, an app list per tenant, and a lookup on every request to decide which
one is in play. That is not a feature bolted on later — it is a different
framework wearing the same module names.

## Decision

podpack builds a single site per running instance. It does not serve several
domains from one process and there is no host-based routing. Running two sites
means two deployments: the same packages, different config and different
containers. There is no `SERVER_NAME` handling and no site registry, and none is
to be added.

## Consequences

The bought simplicity is real and is spent immediately. `_current_app_name()` in
`paths.py` resolves an app from `request.blueprint` alone, with no tenant to
disambiguate first. `target_metadata()` in `migrations.py` returns `db.metadata`
without building a Flask app at all, which is why a broken factory is not also a
broken migration.

The cost is paid per site, in operations. Two sites on one host are two images,
two PostgreSQL containers and two sets of bind mounts. `SITE_NAME` names the
compose project and the image (`compose.yaml:1`, `:125`, `:148`), so names cannot
collide — but ports are not covered and must still be chosen by hand per
deployment. A site wanting ten domains gets ten of everything, and the memory
that would have been one shared process is not recoverable later without the
registry this record refuses.

`db.metadata` being global also means autogenerate sees exactly the enabled
apps, so running it with an app disabled proposes dropping that app's tables.
Under multi-tenancy that footgun would have been a per-tenant one, which is
worse, but it is not gone.

## Alternatives considered

**Host-based routing inside one process.** Rejected because the saving is a
process and the price is a registry in `config.py`, `paths.py` and
`migrations.py` at once, plus multiple alembic heads. Two deployments cost
containers, which are cheap; a registry costs correctness in the parts of the
framework that currently have none of it to spare.

**Leaving it undecided.** This is what commit `b962bbe` (2026-08-05) closed. The
code was already single-site; nothing changed there, and nothing had to be
removed. What changed was that the assumption stopped being inferable only from
its absence.
