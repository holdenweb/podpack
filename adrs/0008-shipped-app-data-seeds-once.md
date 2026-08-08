# ADR-0008: Shipped app data seeds once

**Status:** Accepted

**Date:** 2026-08-08

## Context

An app can bring data with it — `podpack_notes` ships `welcome.md` — but the
copy it runs against cannot be the one inside its own package. The package is
read-only to the process reading it, and the host is where backups, editing and
deployment expect an app's data to be. So a copy has to reach the host, and
something has to decide how often. Every boot and once are the two answers, and
they are not close.

## Decision

`_seed_data()` in `src/podpack/registry.py` copies an app's shipped `data/` into
`<data root>/<name>/`, and only when that directory is empty. The app reads the
host copy from then on.

Emptiness is the same signal the database bootstrap uses. Scripts in
`/docker-entrypoint-initdb.d` — here `db-init/` — run only while the cluster's
data directory is empty, and since that directory lives on the host, "first
time" means first time on this machine rather than every time the container is
recreated. Re-arming seeding means deleting the app's directory under
`hostdata/apps/`, exactly as re-arming the bootstrap means deleting the cluster.

## Consequences

Editing a shipped file on the host changes behaviour with no rebuild, which is
the property the mounted config files have and the reason for the rule.
`7388ef7` verified both halves against a live stack: seeded data editable on the
host, and seeding re-arming only when the app's data directory is deleted.
`test_shipped_data_is_seeded_once` holds it there — it edits `welcome.md`,
reinstalls, finds the edit intact, then deletes the file and finds the shipped
text back.

The cost is upgrades. An app that adds a file in a later version never delivers
it to a host that already has the directory, because a directory holding one
stale file is not empty. Nothing compensates on the code side either:
`SiteApp.init` is called on every install, so an app needing genuine first-run
work — generating a key, building an index — has to detect emptiness itself and
reimplement this rule privately.

`b93b67a` records that gap as deferred rather than missed. The trigger is not "a
real app": `pp-pdf` is real and ships no `data/` at all. It is an app that ships
data expected to change with its code. Deciding now means guessing what
versioned seed data has to do with nothing to check the guess against; deciding
then costs one migration.

## Alternatives considered

**Copy on every boot.** Self-healing for upgrades, and it destroys the reason for
copying: the host file would be overwritten each restart, making it read-only in
practice.

**Read from the package, no host copy.** No upgrade problem, but nothing to back
up and nothing the app can write, since the package is read-only to it.

**Version-stamp the seed and merge on change.** The deferred design. It has to
say what happens to a file the host edited and a new version also changed, and
there is no case yet to answer that against.
