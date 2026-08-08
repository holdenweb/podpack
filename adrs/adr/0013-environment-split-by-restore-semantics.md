# ADR-0013: Split the environment by what a restore does to it

**Status:** Accepted

**Date:** 2026-08-08

## Context

One `.env` held values with opposite requirements. `SECRET_KEY` signs sessions
and the tokens in password-reset links, so replacing it voids every one in
flight; change `POSTGRES_DB` or the application role and the site cannot reach
its own data. Host paths, ports and worker count are the reverse — they are
*expected* to differ on a new host, and the old `.env.example` said so of the
whole file: "the ONLY file that should differ between your laptop and a real
host".

So a restore had to hand-edit a copy of the backup — keep the credentials,
change the paths and the port — a manual step in the one procedure that should
have none.

## Decision

Two files, split by what a restore does to them rather than by secrecy. `.env`
holds what a new host changes: `SITE_NAME`, `HOST_DATA_DIR`, `HOST_LOG_DIR`,
`WEB_HOST_PORT`, `WEB_BIND_ADDR`, `POSTGRES_HOST_PORT`, `GUNICORN_WORKERS`.
`secrets.env` holds what must come back verbatim: the superuser and application
credentials, `POSTGRES_DB`, `SQLALCHEMY_DATABASE_URI`, `SECRET_KEY`. Both reach
containers through `env_file:` lists; only `.env` is read for substitution, so
`compose.yaml` names no credential.

The database identity moved as one unit — name, roles and URI in the same file —
rather than the URI being assembled in `compose.yaml` from substituted parts
that could drift from the cluster on disk. `POSTGRES_ADMIN_USER` became
`POSTGRES_USER`, the name the postgres image itself reads, so nothing needs
interpolating to reach it.

## Consequences

The property was checked by drill rather than by reading: the suite was brought
up under a different `SITE_NAME` and port with `secrets.env` byte-identical
throughout, which is exactly the restore-to-a-new-host case.

Restoring is now copy one file, edit the other, and `compose.yaml` stays safe to
commit and to read.

`podman compose config` is the hole: it expands `env_file` contents, so its
output is as sensitive as `secrets.env`. That was found by running it, not by
reading, and a clean compose file makes it easy to forget.

`SQLALCHEMY_DATABASE_URI` restates the role, password and database name sitting
a few lines above it in `secrets.env`, and nothing checks that they agree. Keeping
the identity in one file did not make it one value.

`scripts/prepare-host-dirs.sh` now creates both files from their examples. On a
host that should have restored a backup, that quietly produces working *lab*
credentials and a site that cannot see its data.

The rename to `POSTGRES_USER` breaks any `.env` written before this.

## Alternatives considered

**Keep one file.** Every restore stays an edit of a backup, done under pressure.

**Split by secrecy.** `POSTGRES_DB` and the connection host are not secrets, so
they would have stayed in `.env` and the URI would have been assembled from
interpolated parts — the drift this decision exists to remove.

**Put the credentials in a secret store.** It solves distribution, which was not
the problem, and adds a second thing to back up beside the database and
`HOST_DATA_DIR` ([ADR-0007](0007-per-app-data-and-log-directories.md)).
