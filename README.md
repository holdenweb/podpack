# podpack

A framework for building web sites out of pluggable apps, together with the
container substrate that runs them.

**A site is a config file plus a list of installed apps.** podpack supplies the
application factory, the app registry, the template search order and the
migration wiring. The site supplies its own chrome and its app list. Apps ship
as ordinary Python packages and are installed by name:

```toml
[site]
name = "example.com"
apps = ["podpack_notes"]
```

Adding a feature to a running site is a line in that file and a restart — no
code change, no rebuild, and no change to `compose.yaml`.

**One site per instance.** podpack builds a single site. It does not serve
several domains from one process and there is no host-based routing; running two
sites means two deployments — same packages, different config and different
containers. That limit is deliberate, and it is what buys the simplicity
elsewhere: one `db.metadata`, one alembic history, and one app list to reason
about, rather than a registry keyed by hostname and a migration story per
tenant.

The container suite is arranged so that **no state and no host-specific setting
lives inside a container**: persistent state is bind-mounted from
`$HOST_DATA_DIR`, host-specific configuration read-only from `./config`, and
secrets arrive through the environment. Promotion to a real host is an edit of
`.env` alone.

## Quick start

From the root of this repository:

```bash
./scripts/prepare-host-dirs.sh && podman compose up -d --build
```

Then visit <http://localhost:8458/>, or ask the site where it keeps its state:

```bash
curl -s localhost:8458/_status | python3 -m json.tool
```

That route reports the config file it read, every installed app with its data
and log directories and whether they are writable, and which database, role and
schema it is actually connected as. If a mount or a grant is wrong, it says so.

Shut down with `podman compose down`. Host storage survives; see
[Starting over](#starting-over).

---

# The plugin API

An app is a package exposing **one module-level `site_app`**. Everything else is
convention.

```python
# myapp/__init__.py
from podpack import Section, SiteApp

from .views import blueprint

site_app = SiteApp(
    name="myapp",
    blueprint=blueprint,
    url_prefix="/myapp",
    nav=(Section("My App", "/myapp/"),),
)
```

| Field | Meaning |
| --- | --- |
| `name` | Identifies the app everywhere: blueprint name, template namespace, data and log directory. One name, so knowing an app is installed tells you where all its parts are. |
| `blueprint` | An ordinary Flask `Blueprint`. Give it `template_folder="templates"` if it has templates. |
| `url_prefix` | Where its routes are mounted. `None` mounts at the site root. |
| `nav` | `Section` entries contributed to the site's navigation, in installation order. |
| `init` | Optional `callable(app)`, run before the blueprint is registered, for config keys and services. |

Install it by adding its **import name** to `apps` in the site's config file.
Apps are installed in the order listed: nav entries appear in that order, and an
app's `init` may rely on a service an earlier one registered.

## Templates

Put templates under `templates/<name>/`, and refer to them the same way:

```python
render_template("myapp/index.html")
```

The namespace is what stops two installed apps colliding on `index.html`. The
search order is:

```
site templates  ->  app templates  ->  podpack defaults
```

Flask already searches the application's template folder before any blueprint's,
and the *site* is the application — so a site overrides any app template simply
by shipping one at the same namespaced path. podpack's own templates are
appended last, which is why an app that extends `base.html` renders correctly on
a site that has not written any chrome of its own yet.

## Models

Put them in `models.py`. Nothing needs to import it:

```python
# myapp/models.py
from podpack import db


class Thing(db.Model):
    __tablename__ = "things"
    id = db.Column(db.Integer, primary_key=True)
```

The registry imports that module while installing the app, and defining a
`db.Model` subclass registers it on `db.metadata` as an import side effect. That
import is the whole of model registration — and it is why migrations can see an
app that the migration environment has never heard of. See
[Migrations](#migrations) for the consequence.

Name no schema. The application role's `search_path` points at the `app` schema
it owns, so unqualified names land in the right place and alembic needs no
schema configuration either.

## Data and logs

Every installed app gets a subdirectory of the host-mounted roots, named after
the app:

```
<data root>/<name>/     persistent data the app owns
<log root>/<name>/      logs it writes
```

Resolve them with `podpack.paths.data_dir()` and `log_dir()`, which default to
the app handling the current request. An app never builds these paths itself, so
moving them at deployment time is a change to the environment and nothing else —
and **installing an app never requires a change to `compose.yaml`**, because the
roots are mounted and podpack creates the per-app directories inside them.

File logging comes free: podpack attaches a handler to the app's package logger,
so `logging.getLogger(__name__)` inside the app writes to `<name>.log` as well
as to stdout.

### Shipping data with an app

An app may ship a `data/` directory inside its package. On install, podpack
copies it into that app's host data directory **only if the target is empty**.

That gives the same semantics as the database bootstrap in `db-init/`: "the
first time on this machine", not "every time the container is recreated".
Re-arming it means deleting the app's host data directory. The app then reads
the *host* copy at runtime, so editing a shipped file on the host changes
behaviour with no rebuild — the same property the mounted config files have.

## Configuration

Each app gets a namespace of its own in the site's config file:

```toml
[apps.myapp]
page_size = 20
```

Read it with `podpack.app_config()`, which defaults to the app serving the
current request. podpack never has to know what any of these settings mean.

Secrets do not go here. The split throughout is: **non-secret settings that vary
per host go in `config/`; secrets go in the environment.** Config files are
version-controllable and reviewable; `.env` is not committed.

---

# Migrations

One alembic history for the whole site. The metadata alembic compares against is
built by importing the models of every app the *site configuration* says is
installed, so migrations follow the app list.

```bash
# Generate a revision for whatever changed.
podman compose run --rm migrate alembic revision --autogenerate -m "what changed"

# Applied automatically at startup; run by hand like this.
podman compose run --rm migrate alembic upgrade head
```

Because a revision's directory does not say which app it came from, its
**message should**. See
`alembic/versions/205fc0d0ce92_notes_app_initial_schema.py`.

Building that metadata deliberately does *not* construct a Flask app. The
factory needs a secret key and a database URI before it will run, and coupling
migrations to it would make a broken factory a broken migration too.

### The footgun: autogenerate sees only the apps that are enabled

Run `--autogenerate` with an app missing from `apps` and alembic will faithfully
propose **dropping that app's tables**, because from where it is standing they
are tables no app claims. This is checked behaviour, not a theoretical risk.

Always autogenerate against the full app list. Django avoids this with per-app
migration directories; podpack has one history, and per-app histories
(`version_locations` plus `branch_labels`) are the answer if this ever becomes
painful enough to be worth the extra heads to reason about.

### Adopting an existing database

If the tables already exist, generate the revision against a scratch database
and then baseline the real one rather than trying to apply it:

```bash
alembic stamp head
alembic check     # should report no new upgrade operations
```

---

# The container substrate

## Ports

| Service | Host port | Why not the obvious one |
| --- | --- | --- |
| Flask | `127.0.0.1:8458` | 8456 is the real site's local port; 8457 is the MongoDB lab |
| PostgreSQL | `127.0.0.1:5433` | 5432 is very likely a natively installed `postgres` |

Offset on purpose: a lab that silently binds the production port is a lab that
will one day be mistaken for production. Both bind to loopback only, so neither
is reachable from the network. Change them in `.env` if they still clash.

## Where everything lives

| What | Host location | Container location |
| --- | --- | --- |
| Database cluster | `$HOST_DATA_DIR/postgres/pgdata` | `/var/lib/postgresql/data/pgdata` |
| Per-app data | `$HOST_DATA_DIR/apps/<name>` | `/var/lib/holdenweb/apps/<name>` |
| Per-app logs | `$HOST_LOG_DIR/apps/<name>` | `/var/log/holdenweb/apps/<name>` |
| PostgreSQL log | `$HOST_LOG_DIR/postgres/postgresql.log` | `/var/log/postgresql` |
| Server settings | `config/postgresql.conf` | `/etc/postgresql/postgresql.conf` (ro) |
| Client authentication | `config/pg_hba.conf` | `/etc/postgresql/pg_hba.conf` (ro) |
| Username mapping | `config/pg_ident.conf` | `/etc/postgresql/pg_ident.conf` (ro) |
| Site settings | `config/app.toml` | `/etc/holdenweb/app.toml` (ro) |
| Secrets and wiring | `.env` | environment variables |

`HOST_DATA_DIR` and `HOST_LOG_DIR` default to `./hostdata` and `./hostlogs`
(both gitignored) so the suite is self-contained. On a real host they become
absolute — `/srv/holdenweb/data`, `/var/log/holdenweb` — and nothing else needs
to change.

Apps live under an `apps/` level rather than beside `postgres/` so that the two
ownership fixes cannot reach each other: a single recursive chown of the data
root would take the database's data directory with it.

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
podman compose up -d --build      # after editing src/ or the Containerfile
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

The site logs to stdout, and each app additionally to its own file:

```bash
podman compose logs -f web
tail -f hostlogs/apps/notes/notes.log
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
  there — which is why apps' models name no schema,
- revokes `CREATE` on `public` from `PUBLIC`, making the intent explicit.

The image runs that directory **only while the data directory is empty** — and
since the data directory is on the host, that means "the first time you bring
the suite up on this machine", not "every time the container is recreated".

## Starting over

```bash
podman compose down
rm -rf hostdata hostlogs
./scripts/prepare-host-dirs.sh
podman compose up -d
```

Deleting `hostdata/postgres/pgdata` is what re-arms the database bootstrap;
deleting an app's directory under `hostdata/apps/` re-arms its data seeding.

## How the services fit together

`init-storage` → `postgres` (waits for healthy) → `migrate` → `web`.

`init-storage` is a throwaway root container that hands the bind-mounted host
directories to the unprivileged uids the servers actually run as (999 for
`postgres`, 10001 for the app). Without it the server cannot write to a host
directory it does not own. It is not a privilege escalation: under rootless
podman that "root" is your own user inside a namespace, and on macOS the
ownership change is namespace-local — the host keeps its own ownership.

`migrate` runs `alembic upgrade head` once and exits, gated by
`service_completed_successfully`, so `web` cannot start against a stale schema.
Doing it here rather than in the application also removes a race: gunicorn
starts several workers at once, and anything creating tables at boot means the
losers crash on tables a sibling has just made.

The database healthcheck is `pg_isready -U … -d …` rather than a bare
`pg_isready`. The flags matter: without them it reports the *server* is
accepting connections before the bootstrap has finished creating the
application's database, and everything downstream starts too early.

The web healthcheck runs [`container/healthcheck.py`](container/healthcheck.py)
as a **script file**, not a `python -c` one-liner: podman splits `["CMD", ...]`
healthcheck arguments on whitespace, so an inline probe arrives mangled and dies
with a SyntaxError — reporting the container unhealthy however well it is
actually running.

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

## Development

```bash
uv sync
uv run pytest
```

The tests cover what the registry promises — that the app list is configuration
rather than code, that models reach `db.metadata`, that template namespacing and
site override both work, that data seeds once and re-arms on deletion, and that
the migration environment needs no Flask app.

There is a MongoDB sibling of this substrate, near-identical in shape and on
different ports so the two can run side by side. It stayed in the holdenweb.com
working tree when this project was extracted.
