# Containerised Flask + PostgreSQL lab

A podman suite running a Flask app and a PostgreSQL server, arranged so that
**no state and no host-specific setting lives inside a container**. The point is
to exercise production-shaped code locally: the same compose file, images and
config files can be promoted to a real host by editing `.env` alone.

This is the PostgreSQL sibling of [../podman](../podman), which does the same
thing with MongoDB. The two are deliberately near-identical in shape, and use
different ports so they can run side by side. Since the real holdenweb
application is a Postgres application, this one goes further and uses the same
database stack it does: **Flask-SQLAlchemy over psycopg2, wired up through
`SQLALCHEMY_DATABASE_URI`**.

## Quick start

```bash
cd podman2 && ./scripts/prepare-host-dirs.sh && podman compose up -d --build
```

Then:

```bash
curl -s localhost:8458/ | python3 -m json.tool
```

The index route reports where each piece of its state came from — the config
file it read, whether the upload mount is writable, and which database, role and
schema it is actually connected as. That is the whole point of the demo app: if
a mount or a grant is wrong, this route says so.

Shut down with `podman compose down`. Host storage survives; see
[Starting over](#starting-over).

## Ports

| Service | Host port | Why not the obvious one |
| --- | --- | --- |
| Flask | `127.0.0.1:8458` | 8456 is the real site's local port; 8457 is the Mongo lab |
| PostgreSQL | `127.0.0.1:5433` | 5432 is very likely a natively installed `postgres` |

Offset on purpose: a lab that silently binds the production port is a lab that
will one day be mistaken for production. Both bind to loopback only, so neither
is reachable from the network. Change them in `.env` if they still clash.

## Where everything lives

| What | Host location | Container location |
| --- | --- | --- |
| Database cluster | `$HOST_DATA_DIR/postgres/pgdata` | `/var/lib/postgresql/data/pgdata` |
| Uploaded files | `$HOST_DATA_DIR/uploads` | `/var/lib/holdenweb/uploads` |
| PostgreSQL log | `$HOST_LOG_DIR/postgres/postgresql.log` | `/var/log/postgresql` |
| App log | `$HOST_LOG_DIR/web/app.log` | `/var/log/holdenweb` |
| Server settings | `config/postgresql.conf` | `/etc/postgresql/postgresql.conf` (ro) |
| Client authentication | `config/pg_hba.conf` | `/etc/postgresql/pg_hba.conf` (ro) |
| Username mapping | `config/pg_ident.conf` | `/etc/postgresql/pg_ident.conf` (ro) |
| App settings | `config/app.toml` | `/etc/holdenweb/app.toml` (ro) |
| Secrets and wiring | `.env` | environment variables |

`HOST_DATA_DIR` and `HOST_LOG_DIR` default to `./hostdata` and `./hostlogs`
(both gitignored) so the lab is self-contained. On a real host they become
absolute — `/srv/holdenweb/data`, `/var/log/holdenweb` — and nothing else needs
to change.

The split worth internalising: **non-secret settings that vary per host go in
`config/`; secrets go in the environment.** Config files are
version-controllable and reviewable; `.env` is not committed.

### Why the data directory is a sub-directory

PostgreSQL refuses to start unless its data directory is mode `0700`, and the
permissions of a *bind mount point* belong to the host — on macOS virtiofs they
come out world-writable. So the host directory is mounted at
`/var/lib/postgresql/data` and `PGDATA` points one level deeper, at
`.../data/pgdata`, which `initdb` creates itself and therefore gets right:

```console
$ ls -ld hostdata/postgres hostdata/postgres/pgdata
drwxr-xr-x  hostdata/postgres/          <- the mount point, host's business
drwx------  hostdata/postgres/pgdata/   <- created by initdb, 0700 as required
```

Do not create `pgdata` yourself; `prepare-host-dirs.sh` deliberately does not.

## Changing things

Three loops, depending on what you touched:

```bash
podman compose restart web       # after editing config/app.toml
podman compose restart postgres  # after editing config/postgresql.conf

# after editing config/pg_hba.conf only -- no restart needed.
# `-u postgres` is required: pg_ctl refuses to run as root.
podman compose exec -u postgres postgres pg_ctl reload
```

```bash
podman compose up -d              # after editing .env (recreates containers)
podman compose up -d --build      # after editing app/ or the Containerfile
```

Editing a mounted config file needs no rebuild and no image change, which is
exactly the behaviour you want on a real host. `pg_hba.conf` is the one that can
be applied without even a restart.

### `ALTER SYSTEM` will fail, by design

Because `postgresql.conf` is mounted read-only from the host, `ALTER SYSTEM`
cannot write to it. That is the intended trade: configuration belongs to the
host and to version control, not to whoever last had a superuser session.

Note also that a config file outside the data directory means `initdb`'s own
generated `postgresql.conf` is ignored **entirely** — so anything you need must
be set in `config/postgresql.conf` or left at PostgreSQL's built-in default.
That is also why `hba_file` and `ident_file` are named explicitly there: they
default to sitting beside the config file, and all three are mounted together.

## Reading the logs

PostgreSQL is configured with `logging_collector = on`, writing to a **file on
the host**, matching how it would be run in production — so `podman logs` shows
little for it beyond startup:

```bash
tail -f hostlogs/postgres/postgresql.log
```

The Flask app logs to both stdout and the host, so either works:

```bash
podman compose logs -f web
tail -f hostlogs/web/app.log
```

## Talking to the database directly

From the host, through the published port:

```bash
PGPASSWORD=holdenweb-app-password psql -h 127.0.0.1 -p 5433 -U holdenweb_app -d holdenweb
```

Or without a local psql installed:

```bash
podman compose exec postgres psql -U labadmin -d holdenweb
```

`holdenweb_app` is the *application* role: it can log in, connect to one
database, and owns one schema. The admin credentials in `.env` are used exactly
once, by the bootstrap below, and are never given to the app.

## First-run bootstrap

[`db-init/01-create-app-user.sh`](db-init/01-create-app-user.sh) creates the
least-privileged application role. It:

- creates the `holdenweb_app` login role and grants it `CONNECT`,
- creates a schema `app` **owned by** that role, so it can create its own
  tables without any privilege over the rest of the database,
- sets the role's `search_path` to that schema, so unqualified table names land
  there — which is why the model in [`app/app.py`](app/app.py) names no schema,
  exactly as the real application's models do,
- revokes `CREATE` on `public` from `PUBLIC`, making the intent explicit.

The image runs that directory **only while the data directory is empty** — and
since the data directory is on the host, that means "the first time you bring
the lab up on this machine", not "every time the container is recreated".

## Schema creation, and what the real app does instead

The demo app calls `db.create_all()` on startup, guarded because gunicorn starts
several workers at once and the loser of that race would otherwise crash on a
table another worker just created.

That is fine for a lab with one table. The real holdenweb package uses
**alembic**, which is the right answer once there is a schema worth migrating.
If you point this lab at the real application, replace the `create_all` call
with `alembic upgrade head` — either as a one-shot compose service that runs
before `web` (the same `service_completed_successfully` pattern
`init-storage` uses) or in the container's entrypoint. Note that the `app`
schema owner and `search_path` above mean alembic needs no schema
configuration either.

## Starting over

```bash
podman compose down
rm -rf hostdata hostlogs
./scripts/prepare-host-dirs.sh
podman compose up -d
```

Deleting `hostdata/postgres/pgdata` is what re-arms the bootstrap script.

## How the services fit together

`init-storage` → `postgres` (waits for healthy) → `web`.

`init-storage` is a throwaway root container that hands the bind-mounted host
directories to the unprivileged uids the servers actually run as (999 for
`postgres`, 10001 for the app). Without it the server cannot write to a host
directory it does not own. It is not a privilege escalation: under rootless
podman that "root" is your own user inside a namespace, and on macOS the
ownership change is namespace-local — the host keeps its own ownership.

The healthcheck is `pg_isready -U … -d …` rather than a bare `pg_isready`. The
flags matter: without them it reports the *server* is accepting connections
before the bootstrap has finished creating the application's database, and `web`
starts too early.

## Running on Linux

Two differences on a real Linux host:

- **SELinux (RHEL, Fedora, CentOS Stream).** Append `,Z` to the read-write bind
  mounts and `,z` to the read-only ones in [compose.yaml](compose.yaml) — e.g.
  `${HOST_DATA_DIR}/postgres:/var/lib/postgresql/data:Z`. Without a label,
  SELinux denies the container access. The mount points are commented in the
  file.
- **Ownership.** `init-storage` handles it, but if you prefer to pre-create the
  directories yourself, `prepare-host-dirs.sh` does the equivalent
  `podman unshare chown` on Linux.

## Compose front-ends

Both work:

- `podman compose` — delegates to Docker Compose v2 if installed, which has the
  most complete support for `depends_on` conditions. Recommended.
- `podman-compose` — also works. It names containers with underscores
  (`holdenweb-lab-pg_web_1`) rather than hyphens, so don't mix the two
  front-ends against the same project without taking the stack down first.

## What the demo app is, and is not

[`app/app.py`](app/app.py) exists to prove the plumbing: it reads host config,
writes to PostgreSQL through Flask-SQLAlchemy, writes to the host uploads
directory, and reports on all three. It is **not** the real holdenweb
application — but unlike the Mongo lab it uses the same database stack, so
what works here has a fair chance of working there.

To point this lab at the real application, build the `web` service from the
repository root using the pattern in the top-level [Dockerfile](../Dockerfile)
(uv + uwsgi), keep `SQLALCHEMY_DATABASE_URI` pointed at the `postgres` service,
and swap `create_all` for alembic as described above.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Where each piece of state lives; database, role and schema in use |
| `GET` | `/healthz` | 200 only if `SELECT 1` succeeds; 503 otherwise |
| `GET` | `/notes` | List stored notes, newest first |
| `POST` | `/notes` | `{"text": "..."}` → stores a row |
| `POST` | `/uploads/<name>` | Request body → a file in the host uploads directory |

## Proving persistence

The claim worth testing, since it is the whole reason the suite exists:

```bash
curl -sX POST -H 'Content-Type: application/json' -d '{"text":"survives"}' localhost:8458/notes
curl -sX POST --data-binary 'hello' localhost:8458/uploads/probe.txt
podman compose down          # both containers destroyed
podman compose up -d
curl -s localhost:8458/notes # the row is still there
cat hostdata/uploads/probe.txt
```
