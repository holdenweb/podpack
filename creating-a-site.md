# Creating a podpack site from scratch

Every command here was run while writing this, and the whole document was then
followed again from scratch. The errors quoted are real ones, produced by
provoking them rather than transcribed from the source.

That is a claim with a shelf life, and it has expired once already: between
podpack 0.5.0 and 0.9.0 this document drifted until it could not be followed at
all — it asked for an environment podpack would no longer start on, and then for
a migration podpack would no longer write. Both had been true for months. If you
are reading this against a newer podpack than the heading below suggests, treat
it as a document, not as a test result.

A site is a Python package that calls podpack's factory, a TOML file saying what
it is and what it installs, and — to deploy it — a copy of the container
substrate.

**`~/sites/podpack-demo` is the worked output of this document**, built by
following it rather than by copying podpack. If these instructions stop working,
that site stops building, which is the point of keeping it.

This document installs an app that already exists. To *write* one, see
[writing-an-app.md](writing-an-app.md).

**Work in one shell throughout.** From step 7 onwards the environment comes
from `dev.env`, sourced into that shell; a new terminal between sections fails
with no hint as to why.

**Verified against podpack 0.9.0b2.**

---

## Part 1 — a site on your laptop

### 1. Make the project

```bash
mkdir mysite && cd mysite
mkdir -p config src/mysite/templates
```

Everything below is run from `mysite/`, and the paths in the config are relative
to it.

### 2. Depend on podpack

```toml
# pyproject.toml
[project]
name = "mysite"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["podpack", "podpack-notes"]

[build-system]
requires = ["uv_build>=0.8.4,<0.9.0"]
build-backend = "uv_build"

[tool.uv.sources]
podpack-notes = { git = "https://github.com/holdenweb/podpack-notes.git" }
```

**podpack needs no source entry**: it publishes to PyPI on every release tag, so
an ordinary version specifier resolves it. Say `podpack>=0.9.0b2` if you want a
pre-release — uv permits one only where the specifier names it, which opts that
package in and nothing else.

`podpack-notes` is the example app this guide installs, and still comes from git
because it has no PyPI publisher yet; a site that wants no app can drop the line
and the dependency. A git source resolves to what is **on the remote**, which is
not necessarily what is on your disk.

*Working on podpack itself?* Then `podpack = { path = "/path/to/podpack",
editable = true }` is what you want — but only for local work. A path source
cannot survive a container build, and Part 2 says what that failure looks like.

Add one more section while you are here, because the reason only shows up on a
real host:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

Without it a bare `pytest` walks the whole site, including `hostdata/`.
PostgreSQL insists its data directory is mode 0700 and `init-postgres` chowns
it to the container's postgres uid — which under rootless podman is a subuid
the invoking user cannot read, so collection aborts with `PermissionError`
before a single test runs. It cannot happen on macOS, where that directory
belongs to the user running podman machine.

### 3. Write the factory

```python
# src/mysite/__init__.py
import podpack


def create_app():
    return podpack.create_app(site_package="mysite")
```

`site_package="mysite"` makes this package's `templates/` and `static/` the
site's own. Extensions belonging to the site rather than to a feature — mail,
login, session policy — go in an `init` callable passed alongside; see
[ADR-0025](adrs/0025-the-site-wires-its-own-extensions.md).

### 4. Give it some chrome

```html
<!-- src/mysite/templates/base.html -->
<!doctype html><title>{{ site.name }}</title>
<h1>{{ site.name }}</h1>
<nav>{% for s in sections %}<a href="{{ url_for(s.endpoint) }}">{{ s.label }}</a>{% endfor %}</nav>
{% block content %}{% endblock %}
```

`site` is the `[site]` table from your config; `sections` is the navigation the
installed apps contributed. Apps extend `base.html` without knowing whose it is,
so this one file gives every installed app your look.

### 5. Say what the site is

```toml
# config/app.toml
[site]
name = "My Site"
environment = "local"
apps = ["podpack_notes"]

[apps.notes]
page_size = 20
```

Three spellings of one thing, and they are not interchangeable:
`podpack-notes` is the **distribution** you depend on, `podpack_notes` the
**import name** that goes in `apps`, and `notes` the app's **own name** — its
blueprint's — which keys `[apps.notes]`, `[site.mounts]` and its directories on
disk.

### 6. Install, and take the substrate

```bash
uv sync
uv run podpack substrate init                       # postgres alone
uv run podpack substrate init --services mongodb    # postgres and mongodb
```

The substrate is the set of files a site **copies rather than imports**: the
alembic environment, the container files Part 2 deploys with, and the
`.example` files the next two steps read. `init` derives the site's package
from `pyproject.toml`, says what it resolved, and records what it installed in
`substrate.json`, which you commit.

It comes this early because everything after it needs something it delivers.

`--services` names the *optional* stores this site also runs. PostgreSQL is not
among them: `db`, the migration history and the site's login tables are all
SQL, so podpack will not start without it
([ADR-0029](adrs/0029-postgresql-is-required-mongodb-is-optional.md)). Which
optional stores to run is the site owner's decision and nobody else's — an app
cannot declare that it needs one. It is not a decision you are stuck with:
`podpack substrate services --add mongodb` enables another later, and `podpack
substrate services` says what a site runs today. Note that *both* commands
above write the mongodb files; only `--services mongodb` names the overlay in
`COMPOSE_FILE`, and that is what decides whether it runs.

Two conventions arrive in the `.gitignore` it writes, worth knowing before you
start dropping files anywhere. **`scripts/` is podpack's** — seven files it
manages: `dev.sh`, `up.sh`, `prepare-host-dirs.sh`, `configure-host.py`,
`backup.sh`, `restore.sh` and `verify-backup.sh`. **`scratch/` is yours** —
experiments, one-off utilities, notes — ignored so it never reaches a commit or
a build context. The separation is not load-bearing (the command walks a
manifest and never sweeps a directory) but a mixed `scripts/` is an invitation
to a mistake: holdenweb.com kept seven personal scripts beside the managed ones
for a year.

Everything it writes is yours to edit; `podpack substrate status` will tell
you, file by file, how your copy relates to the installed podpack, and
`podpack substrate upgrade` brings a copy forward when podpack ships fixes (see
the README's "Keeping the substrate current").

### 7. Point it at a local PostgreSQL

`substrate init` has just delivered `dev.env.example`, which carries every
variable podpack requires and the reason for each. It is the counterpart of
`.env` and deliberately not part of it: `.env` is compose's, and a local run
that borrowed it would be one edit away from pointing development at
production's database.

```bash
cp dev.env.example dev.env       # scripts/dev.sh does this for you on first run
```

Edit it. What must be real is the database it names, on a PostgreSQL **you**
installed:

```bash
createuser --pwprompt mysite_app
createdb --owner mysite_app mysite
```

podpack will not run those for you: creating a database on a server it does not
own is not something a run script should do behind your back. `scripts/dev.sh`
prints them again if it cannot connect.

> **Not SQLite — and podpack now declines rather than letting you find out
> later.** A revision autogenerated against SQLite can be one PostgreSQL
> rejects, `ALTER COLUMN` being PostgreSQL-only syntax, so:
>
> ```
> RuntimeError: refusing to autogenerate against sqlite: this site deploys on
> postgresql, and a revision written here can be one PostgreSQL rejects. Point
> SQLALCHEMY_DATABASE_URI at a PostgreSQL first -- `dev.env` names your local
> one. Applying migrations (`alembic upgrade head`) is not restricted.
> ```
>
> Authoring against the engine you deploy on is the whole point of authoring on
> the host ([ADR-0011](adrs/0011-migrations-are-authored-on-the-host.md)).

Load it into this shell, and keep that shell for the next two steps:

```bash
set -a; . ./dev.env; set +a
```

### 8. Create the schema

An app's tables are the *site's* to migrate — one history for the whole site,
per [ADR-0009](adrs/0009-one-alembic-history.md) — so alembic lives here, not
in the app. The shipped `alembic/env.py` needs no editing: it reads
`SQLALCHEMY_DATABASE_URI` from the environment, the same variable the running
site uses, so the two can never disagree about which database they mean, and
finds your config file through `PODPACK_CONFIG`.

```bash
uv run alembic revision --autogenerate -m "installed app schema"
uv run alembic upgrade head
```

```
INFO  [alembic.autogenerate.compare.tables] Detected added table 'notes'
INFO  [alembic.autogenerate.compare.tables] Detected added table 'role'
INFO  [alembic.autogenerate.compare.tables] Detected added table 'user'
INFO  [alembic.autogenerate.compare.tables] Detected added table 'roles_users'
INFO  [alembic.runtime.migration] Running upgrade  -> <id>, installed app schema
```

Four tables, not one. `notes` is the app's; `role`, `user` and `roles_users`
are podpack's, because login is core
([ADR-0033](adrs/0033-login-is-core.md)) — a site gets them whether or not it
installs anything that uses them.

`target_metadata()` imports the models of exactly the apps your config lists.
**Always autogenerate with your full app list enabled** — with one disabled,
alembic will faithfully propose dropping its tables.

The site also creates, the first time it starts:

```
devdata/apps/notes/welcome.md      # data the app ships, seeded on first install
devlogs/apps/notes/notes.log       # the app's own log
```

### 9. Make yourself an administrator

`/_status` reports the database identity and every host path, so it answers
only a member of the `admin` role — which a fresh database does not have:

```bash
uv run flask --app mysite users create you@your-real-domain.com --active
uv run flask --app mysite roles create admin
uv run flask --app mysite roles add you@your-real-domain.com admin
```

Three things about that first command, each of which stops it dead:

- **It asks for a password**, and waits. It is not a one-liner, and in a script
  it will sit there until something feeds it.
- **The address must be one that could receive mail.** flask-security checks
  deliverability, so the obvious placeholder is refused —
  `Error creating user. {'email': ['Invalid email address']}` for
  `you@example.com`, whose domain is reserved and publishes no MX record. Use
  your own address; it is your administrator account, so you want it real
  anyway.
- **`--active`**, or the account exists and cannot sign in.

These are flask-security's own commands. Login is podpack's (ADR-0033), so
there is no `models.py` to write, no `Security()` to construct and no predicate
to pass — `create_app` installs `podpack.auth`, and `/_status` asks its
`is_admin`.

Until the role exists and somebody holds it, `/_status` answers 404 to everyone
including you. That is correct for a route publishing your database identity
and every host path, but a 404 is indistinguishable from a missing route, so
podpack says why in the log at every boot rather than leaving you to find out
by querying the role table.

*(A site with its own idea of who counts as an operator may still pass
`create_app(admin=…)`. Few will want to.)*

### 10. Look at it, without containers

```bash
./scripts/dev.sh
```

It makes `dev.env` from the example if you have not, checks it can reach your
PostgreSQL — printing the `createuser`/`createdb` lines if not — applies the
migration history and starts the development server.

`/` is your site, `/notes/` the installed app wearing your chrome, and
`/_status` reports where every piece of state lives.

*(Port 5001 rather than 5000: macOS gives 5000 to AirPlay Receiver, and the
symptom of the clash is 403s that look like an application bug.)*

---

## Part 2 — containerising it

The container files are already in place: `podpack substrate init` wrote them
in step 6, rendered for this site — the Containerfile's gunicorn line names
*your* factory, and the `.example` files carry your site's name and database
identity. (A site that skipped step 6 runs the same command now.) Two things
remain yours to check:

| File | Check |
| --- | --- |
| `.env.example` | ports that clash with nothing already running on this machine (`--web-port`/`--db-port` at init, or edit now) |
| `pyproject.toml` | the dependency sources — see below |

Edit the `.example` files, not `.env` and `secrets.env`: the next step creates
those from them.

### Where each dependency comes from

A `path` source cannot work in a container. The build context has no such path:

```
error: Failed to determine installation plan
  Caused by: Distribution not found at: file:///path/to/podpack
```

So a site you intend to deploy has three kinds of dependency, and only the last
needs a source entry at all:

| | Source | Why |
| --- | --- | --- |
| `podpack` | none — a plain version specifier | published to PyPI on every release tag |
| apps with a publisher | none | same |
| apps without one yet | `{ git = "https://github.com/…" }` | `podpack-notes` is one of these |

```toml
[tool.uv.sources]
podpack-notes = { git = "https://github.com/holdenweb/podpack-notes.git" }
```

**Then run `uv lock`.** Editing `[tool.uv.sources]` alone is not enough — the
lockfile still carries the old source and the Containerfile builds with
`--frozen`, so the build fails with exactly the error above and no indication
that the fix was one command away. Moving a git-sourced dependency forward later
needs `uv lock --upgrade-package X --refresh-package X`: uv caches the resolved
ref, and without `--refresh-package` it moves silently or not at all.

This is what the `git` layer in the Containerfile is for: uv shells out to a
real `git` to fetch these, and the slim base image has none.

### Deploying to a host that is not your laptop

Everything above assumes a lab. A real host — one with a proxy in front and a
port somebody else allocated — has one more command and one more setting.

```bash
git clone <this repo> && cd mysite
python3 scripts/configure-host.py --port <the port the host allocated>
```

`configure-host.py` writes `.env` and `secrets.env` at 0600, generating fresh
secrets, detecting SELinux from `/sys/fs/selinux/enforce` and setting the mount
relabelling accordingly, and checking the prerequisites — the Compose v2
provider, `podman.socket`, lingering. It **refuses to overwrite** either file,
and `--check` inspects without writing anything, which is the one to reach for
on a host somebody else set up.

Then, in `.env`, if anything proxies to this site:

```bash
PODPACK_PROXY_HOPS=1
```

Without it every URL the site builds with `_external=True` comes out `http://`,
because the proxy forwards plain HTTP and podpack will not believe an
`X-Forwarded-Proto` it has not been told to trust. Nothing logs it and no page
looks wrong; the first thing to carry such a URL is usually a password-reset
mail, so it surfaces in somebody's inbox. `/_status` reports `proxy`, with the
header as received beside the count trusted, which is how to check it without
sending yourself a reset. The count is the number of values in the header, not
the number of proxies — see
[ADR-0036](adrs/0036-the-host-says-whether-to-believe-the-proxy.md).

### Backing it up

The substrate ships the regime as well as the deployment:

```bash
./scripts/backup.sh              # dumps every service, archives per-app data
./scripts/verify-backup.sh       # proves the archive, by restoring it elsewhere
./scripts/restore.sh <backup>    # puts it back
```

Apps declare what of theirs has to come back
([ADR-0035](adrs/0035-apps-declare-what-is-theirs-to-back-up.md)), so an
ordinary app is included without saying anything. `backup.sh` writes a
directory; where that directory then goes, and how long it is kept, is yours.

### Bring it up

```bash
./scripts/prepare-host-dirs.sh   # host directories, and .env from the example
git init                         # optional -- see below
./scripts/up.sh
```

```
mysite-postgres-1   Up 21 seconds (healthy)
mysite-web-1        Up 14 seconds (healthy)
```

`scripts/up.sh` always rebuilds and stamps the commit into the image, which
`/_status` reports as `build_commit`. Outside a git repository it says
`building from unknown` and stamps `unknown` — harmless, but `git init` first if
you want that question answerable later.

---

## What is still awkward

Honest notes, from doing this rather than imagining it.

- **The substrate upgrade delivers parameters, not prose — and nothing at
  all to a seeded file.** `podpack substrate upgrade` brings managed files
  forward and appends newly-introduced configuration variables, but the
  *seeded* files (`.gitignore`, the `.example` pair, the README stub) became
  yours on delivery and are never touched again
  ([ADR-0026](adrs/0026-the-substrate-ships-in-the-package-and-upgrades-by-manifest.md)).
  So an improvement to a seed reaches new sites only, and an existing site
  copies it by hand — `podpack substrate diff` will not show it, because a
  seeded file is not compared. This is not a footnote. When 0.9.0b2 added
  `PODPACK_PROXY_HOPS` the whole of its documentation went into
  `env.example`, so holdenweb.com — which has a proxy in front of it and
  needs the setting — will never be told: its own `.env.example` mentions the
  variable zero times and always will, while podpack's ships it. The site was
  upgraded, reported every file `ok`, and remained silently short of the one
  thing the release was for. Watching podpack's own
  `src/podpack/substrate/data/` is the only way to notice.
- **Apps cannot ship migrations.** Every site installing an app regenerates that
  app's tables in its own history. Fine while a schema is stable; it is the same
  gap as the deferred app-upgrade problem.
- **Nothing checks that `SITE_NAME` in `.env` matches `name` in `app.toml`.**
  Compose cannot read TOML, which is the only reason the name is written twice.
- **The `adrs/…` links above resolve only inside podpack's repository.** Copy
  this file next to your site and they break.
