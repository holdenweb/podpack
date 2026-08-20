# ADR-0027: The database port is published only on request

**Status:** Accepted

**Date:** 2026-08-13

> **Note, 2026-08-20:** the service and profile called `dbport` below are both
> named `postgres-port` now, renamed by
> [ADR-0028](0028-core-services-are-overlays-the-site-chooses.md) when each
> backing store became an overlay deriving every name from the service's own.
> The forwarder stopped being PostgreSQL's alone and became `<service>-port`,
> so MongoDB has one too. The decision recorded here is unchanged; only the
> spelling is. What works today:
>
> ```bash
> podman compose --profile postgres-port up -d postgres-port
> podman compose --profile postgres-port rm -sf postgres-port
> ```

## Context

The suite published PostgreSQL on `127.0.0.1:${POSTGRES_HOST_PORT}` from the
start, and nothing inside it ever used that port. The application and the
migration service reach `postgres:5432` across the compose network; the
healthcheck runs inside the container. The published port existed for the
host: `psql` at the end of a debugging session, and
`alembic revision --autogenerate`, which compares the models against a live
database and so genuinely needs a connection (ADR-0011).

The cost fell on everything else. ADR-0014 gave each site its own compose
project and image name, which isolates two deployments completely — except
for ports, which it explicitly left as the site's problem to coordinate, and
a clash fails at container start with nothing said beforehand.

That bill arrived on 2026-08-13. holdenweb.com took 5434 for its lab; a
podpack-demo that had used 5434 for a week could no longer come up beside
it, and the fix was to edit a committed file in the site that happened to be
second. Two sites otherwise perfectly isolated could not run together
because of a number neither of them needed.

The original container work had already reached this conclusion and not
acted on it, recording that "deleting the `ports:` block from the database
service is the entire change, since container-to-container traffic is
unaffected by publishing" and that "the benefit is fidelity and removing a
wrong-database footgun, not security" — the port was bound to loopback
throughout.

## Decision

The `postgres` service publishes no port. A separate `dbport` service, under
a Compose profile of the same name, forwards one on request:

```bash
podman compose --profile dbport up -d dbport                  # the default
POSTGRES_HOST_PORT=5439 podman compose --profile dbport up -d dbport
```

It is a `socat` container on the compose network, holding no state, gated on
`postgres` being healthy. Because Compose reads shell variables ahead of
`.env`, the second form chooses a number for one use without editing a
committed file or restarting anything else. `POSTGRES_HOST_PORT` in `.env`
survives as that forwarder's default and nothing else reads it.

The two host-side jobs that wanted the port keep working: `psql` through
`podman compose exec postgres psql`, which needs no port at all, and
autogenerate with the profile up for as long as it takes.

## Consequences

Two sites now need no port coordination for their databases at all — the
clash that prompted this cannot recur, and the only number a deployment must
still choose is the web port, which a managed host dictates anyway.

The default arrangement is also the production-shaped one: a database
reachable only from its own network is what a real deployment looks like,
and a lab that differs from production in a way nobody notices is a lab that
teaches the wrong lesson.

What it costs: an extra step before autogenerate, in the one workflow that
genuinely needs a connection, and one more service in `compose.yaml` — a
profile that most runs never start. Anyone whose habit is `psql -h 127.0.0.1
-p 5433` finds the port gone until they ask for it, which is a real if small
tax on muscle memory. And a forwarder is a hop: it publishes 5432 from
inside its own container, so a connection failure has one more place to be.

## Alternatives considered

- **Keep publishing and document the coordination.** What we had. The
  documentation existed (ADR-0014 says ports are not isolated); the clash
  happened anyway, because nothing enforces reading it.
- **Publish on a port derived from `SITE_NAME`** — a hash into a range.
  Removes the coordination and adds an unpredictable number nobody can
  remember or type, and collisions become rarer rather than impossible,
  which is the worst combination for a footgun.
- **Drop the port outright, with no forwarder.** Simplest file, and it makes
  autogenerate need a scratch database — which the README already documents
  for adopting an existing schema. Rejected because it turns the one
  legitimate host-side use into a detour, for a service that costs nothing
  while it is not running.
- **Put the port back only in a development override file**
  (`compose.override.yaml`). Equivalent in effect, but it is a file a site
  copies and then owns, so podpack could never fix it later — exactly the
  drift ADR-0026 exists to end.
