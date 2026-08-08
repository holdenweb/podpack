# ADR-0020: A root one-shot container fixes bind-mount ownership

**Status:** Accepted

**Date:** 2026-08-08

## Context

Everything this suite persists lives on the host and is bind-mounted in, so it
can be backed up, inspected and restored without asking podman for it. Bind
mounts arrive owned by whoever owns them on the host — `sholden:staff` here —
and neither server runs as that user. `postgres` drops to uid 999, fixed by the
upstream image; the web image creates and runs as uid 10001
(`Containerfile:91-94`). A server that cannot write its own data directory fails
at startup, and on a bare machine nothing has fixed it yet.

PostgreSQL adds a sharper constraint. It refuses to start unless its data
directory is mode 0700, and the permissions of a bind mount *point* belong to
the host: under macOS virtiofs `/var/lib/postgresql/data` presents as
`drwxrwxrwx` inside the container while the host directory is `drwxr-xr-x`.
Nothing the container does changes that.

## Decision

`init-storage`, a busybox container running as `0:0`, chowns the four mounted
roots — PostgreSQL's data and log directories to 999, the app roots to 10001 —
and exits; `postgres` and `web` both gate on it with
`depends_on: {condition: service_completed_successfully}`. `PGDATA` is
`/var/lib/postgresql/data/pgdata`, one level inside the mount rather than at the
mount point, so `initdb` creates that directory itself and it gets the mode
PostgreSQL insists on. Nobody pre-creates `pgdata` — `prepare-host-dirs.sh`
deliberately does not.

## Consequences

Every deployment carries a root container at the head of its dependency graph,
and the whole guarantee rests on `depends_on` conditions being honoured. Under
`podman-compose` they are not, and the servers start alongside the chown rather
than after it ([ADR-0016](0016-require-the-compose-v2-provider.md)).

It is not privilege escalation. On the rootless Linux hosts this deploys to,
that root is the operator's own user in a namespace and 999/10001 are their
subuids. On macOS the change is namespace-local: after four hours of the stack
running, `hostdata/postgres` is still `sholden:staff`. So the fix is not
permanent, which is why it re-runs on every `up`; the chown is idempotent, but
it is recursive over the whole database directory and its cost grows with the
data.

Two chowns rather than one is what forces apps under an `apps/` level: a single
recursive chown of the data root would take `pgdata` with it
([ADR-0007](0007-per-app-data-and-log-directories.md)).

## Alternatives considered

**Chown on the host in `prepare-host-dirs.sh`.** The repo does this, guarded by
`uname -s` so it runs on Linux only, via `podman unshare chown`. It cannot help
on macOS — ownership set inside never reaches the host filesystem — and it needs
an operator to have run the script. It stays as the belt to `init-storage`'s
braces.

**Named volumes instead of bind mounts.** podman would own the permissions and
this problem would not exist. State would stop being visible on the host, which
is the whole point of the arrangement.

**Run the servers as root.** Undoes the privilege drop the images perform
themselves, to avoid a container that exits in under a second.
