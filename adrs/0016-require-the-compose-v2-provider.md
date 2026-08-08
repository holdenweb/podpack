# ADR-0016: Require the Compose v2 provider

**Status:** Accepted

**Date:** 2026-08-08

## Context

Nothing inside the images sequences this stack. Every ordering guarantee is a
`depends_on` condition in `compose.yaml`: `init-storage` chowns the bind mounts
as root and has to finish before servers that have dropped to uid 999 and 10001
([ADR-0007](0007-per-app-data-and-log-directories.md)); `migrate` waits on
`postgres` being `service_healthy`, and `web` waits on `migrate` having
completed ([ADR-0011](0011-revisions-authored-on-the-host.md)).

The README said both front ends worked — `podman-compose` "also works", a
sentence inherited from the original lab that nobody had ever run. `98f5073`
corrected it, because Opalstack's own container tutorial uses `podman-compose`
and Opalstack is where this deploys
([ADR-0015](0015-postgresql-stays-in-a-container.md)). Measured on one file, a
service sleeping five seconds and a second gated on its completion:

```console
podman-compose:  ONCE-START 626  AFTER-START 626  ONCE-END 631
podman compose:  ONCE-START 633  ONCE-END 638     AFTER-START 638
```

The gate is not slow under `podman-compose`, it is absent: the gated service
started in the same second. So `init-storage` stops preceding the servers and
the ownership problem returns, `web` stops waiting for `migrate` and can serve
against a schema that was never created, and nothing waits for PostgreSQL to
accept connections. None of that is reported. The stack comes up, and works or
does not depending on timing.

## Decision

The suite requires `podman compose` delegating to Docker Compose v2.
`podman-compose` is not an alternative front end, it is a broken one, and the
README says so where it used to say the opposite. `scripts/up.sh` runs `podman
compose up -d --build`, and the ordering in `compose.yaml` is written assuming
the conditions are honoured rather than defended against.

## Consequences

Deploying to Opalstack now contradicts their own tutorial at the first command,
so their instructions cannot be followed as written; the README's Opalstack
section carries the correction, and a host that will not install the v2 provider
is left sequencing the phases by hand.

Nothing enforces this. There is no check that the caller used the right front
end, and the failure mode is silence — which is exactly why the wrong claim
survived in the README for as long as it did.

The two front ends name containers differently, underscores against hyphens, so
a stack started under one is invisible to the other. Take it down before
switching.

One bug was bought rather than sold: podman splits `["CMD", ...]` healthcheck
arguments on whitespace, and that only surfaces under the v2 provider. The
stricter front end is what exposed the mangled `python -c` probe now living in
`container/healthcheck.py`.

## Alternatives considered

**Make the ordering self-enforcing so either front end works.** The chown needs
root and the servers do not have it; `web` cannot tell whether alembic has run
without doing the migration's job itself.

**Sequence the phases by hand**, bringing up `init-storage`, then `postgres`,
then `migrate`, then `web`. It works under anything and needs a person to get it
right every time. It stays in the README as the fallback, not as the path.

**Go on documenting both.** That is the state `98f5073` corrected: an untested
claim, protecting a front end under which three guarantees disappear without a
word.
