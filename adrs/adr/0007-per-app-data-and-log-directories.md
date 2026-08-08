# ADR-0007: Per-app data and log directories under mounted roots

**Status:** Accepted

**Date:** 2026-08-08

## Context

The substrate mounted one host directory per purpose: `${HOST_DATA_DIR}/uploads`
and `${HOST_LOG_DIR}/web`, named after what the demo app happened to do. Under a
framework whose central claim is that adding a feature is a line in `app.toml`
and a restart, that spelling makes installing an app an edit to `compose.yaml`
and to `scripts/prepare-host-dirs.sh` as well, with a chown line to go with
them — because bind mounts arrive owned by the host user and the servers drop to
unprivileged uids.

Those chowns are the second pressure. `init-storage` hands PostgreSQL's
directories to uid 999 and the app's to 10001, recursively. Any layout putting
app directories beside `postgres/` puts one recursive chown within reach of
`pgdata`, whose mode 0700 PostgreSQL insists on and initdb alone gets right.

## Decision

Each installed app gets `<data root>/<name>/` and `<log root>/<name>/`, created
by `prepare()` in `src/podpack/paths.py` as the app is installed
(`registry.py:140`). Compose mounts the *roots* — `${HOST_DATA_DIR}/apps` and
`${HOST_LOG_DIR}/apps`, handed to the process as `PODPACK_DATA_ROOT` and
`PODPACK_LOG_ROOT` — and never a per-app directory, so nothing in that file
changes when the app list does (7388ef7). Apps live under an `apps/` level
rather than beside `postgres/`, keeping the two chowns out of each other's
reach. Apps ask for their directory through `data_dir()` and `log_dir()` rather
than building paths, and those resolve the app from `request.blueprint`, which
they can do only because there is no tenant to disambiguate first
([ADR-0001](0001-one-site-per-instance.md)).

## Consequences

The roots drift, and legitimately: dropping an app from `apps` deliberately
leaves its data, because uninstalling a feature should not destroy what it was
holding. `/_status` reports what is left as `unclaimed` (42a8366), files as well
as directories, rather than tidying it away. Renaming a blueprint therefore
orphans that app's data under the old name — now visible, but nothing moves it.

The key is the app's name, not the import name: `podpack_notes` writes to
`notes/`, the same trap paid for in
[ADR-0006](0006-mount-points-belong-to-the-site.md).

One chown gives the whole data root to one uid, so apps are not separated from
each other on disk. They already share a process, so no isolation was lost, but
these directories are not a boundary.

Creation happens at startup rather than on first write, so a permissions problem
appears beside the other mount checks instead of on whichever request first
writes something.

The directory's existence doubles as the seeding flag: `_seed_data` copies
shipped `data/` only into an empty target, so an app adding a file in a later
version never delivers it to a host that has already run it. Deferred, not
solved.

## Alternatives considered

**A mount per app**, which is what `uploads/` and `web/` amounted to. The host
layout stays explicit in `compose.yaml`, at the price of the claim the framework
is built on: installing an app becomes three files and a rebuild rather than one
line and a restart.

**Apps flat beside `postgres/`.** One level fewer and one chown — the chown that
takes `pgdata` with it and undoes the mode initdb set.

**Reconciling the disk with the app list at boot**, deleting whatever no app
answers for. Rejected because it makes editing a config line destructive.
Reporting leaves that decision with the person who can tell data from litter.
