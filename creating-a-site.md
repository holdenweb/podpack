# Creating a podpack site from scratch

Every command here was run while writing this, and then the whole document was
followed again from scratch by people who had not seen the project — twice, once
locally and once in containers. Where they tripped, the document changed. The
errors quoted are real ones.

A site is a Python package that calls podpack's factory, a TOML file saying what
it is and what it installs, and — to deploy it — a copy of the container
substrate.

**`~/sites/podpack-demo` is the worked output of this document**, built by
following it rather than by copying podpack. If these instructions stop working,
that site stops building, which is the point of keeping it.

This document installs an app that already exists. To *write* one, see
[writing-an-app.md](writing-an-app.md).

**Work in one shell throughout.** The environment variables set in step 6 are
needed by steps 7 and 8; a new terminal between sections fails with no hint as
to why.

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
podpack = { path = "/path/to/podpack", editable = true }
podpack-notes = { path = "/path/to/podpack-notes", editable = true }
```

Substitute real paths for both. `podpack-notes` is the example app this guide
installs; a site that wants none can drop it from both places.

Path sources are for local work only — see
[Part 2](#part-2--containerising-it), where they cannot work.

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

### 6. Install, and set the environment

```bash
uv sync
```

```bash
export PODPACK_CONFIG=config/app.toml
export SECRET_KEY=dev-only
export SQLALCHEMY_DATABASE_URI="sqlite:///$PWD/dev.db"
export PODPACK_DATA_ROOT=./hostdata/apps PODPACK_LOG_ROOT=./hostlogs/apps
```

> **The absolute path matters — this is the trap worth reading twice.**
> Flask-SQLAlchemy resolves a *relative* SQLite path against the application's
> instance folder; alembic resolves it against the working directory. With
> `sqlite:///dev.db` you get two different files:
>
> ```
> alembic wrote to        ./dev.db
> Flask-SQLAlchemy used   ./src/instance/dev.db
> ```
>
> Migrations then appear to succeed while the site still reports
> `no such table`. `"sqlite:///$PWD/dev.db"` gives both the same file.
> PostgreSQL has no such problem, which is one reason Part 2 uses it.

The site will now start, and creates as it does:

```
hostdata/apps/notes/welcome.md      # data the app ships, seeded on first install
hostlogs/apps/notes/notes.log       # the app's own log
```

Its own pages will still fail until the next step gives them a table.

### 7. Take the substrate, and create the schema

An app's tables are the *site's* to migrate — one history for the whole site,
per [ADR-0009](adrs/0009-one-alembic-history.md) — so alembic lives here, not in
the app. The alembic environment arrives with podpack's **substrate**: the
files a site copies rather than imports, which also include everything Part 2
deploys with. One command lays the whole set down:

```bash
uv run podpack substrate init                       # postgres alone
uv run podpack substrate init --services mongodb    # postgres and mongodb
```

It derives the site's package from `pyproject.toml`, says what it resolved,
and writes the alembic environment, the container files, and starter
`.env.example` / `secrets.env.example` / `.gitignore` files — recording what
it installed in `substrate.json`, which you commit.

`--services` names the *optional* stores this site also runs. PostgreSQL is
not among them: `db`, the migration history and the site's login tables are
all SQL, so podpack will not start without it
([ADR-0029](adrs/0029-postgresql-is-required-mongodb-is-optional.md)).
Which optional stores to run is the site owner's decision and nobody else's
— an app cannot declare that it needs one. It is not a decision you are stuck with —
`podpack substrate services --add mongodb` enables another later, and
`podpack substrate services` says what a site runs today.

Two conventions arrive in that `.gitignore` and are worth knowing before you
start dropping files anywhere: **`scripts/` is podpack's**, holding only the
two files it manages, and **`scratch/` is yours** — experiments, one-off
utilities, notes — ignored so it never reaches a commit or a build context.
The separation is not load-bearing (the command walks a manifest and never
sweeps a directory) but a mixed `scripts/` is an invitation to a mistake:
holdenweb.com kept seven personal scripts beside the two managed ones for a
year. Everything it writes is
yours to edit; `podpack substrate status` will tell you, file by file, how
your copy relates to the installed podpack, and `podpack substrate upgrade`
brings a copy forward when podpack ships fixes (see the README's "Keeping
the substrate current").

The shipped `alembic/env.py` needs no editing: it reads
`SQLALCHEMY_DATABASE_URI` from the environment — the same variable the
running site uses, so the two can never disagree about which database they
mean — and finds your config file through `PODPACK_CONFIG`, defaulting to
`config/app.toml`.

```bash
uv run alembic revision --autogenerate -m "installed app schema"
uv run alembic upgrade head
```

```
INFO  [alembic.autogenerate.compare.tables] Detected added table 'notes'
INFO  [alembic.runtime.migration] Running upgrade  -> <id>, installed app schema
```

`target_metadata()` imports the models of exactly the apps your config lists.
**Always autogenerate with your full app list enabled** — with one disabled,
alembic will faithfully propose dropping its tables.

### 8. Make yourself an administrator

`/_status` reports the database identity and every host path, so it answers
only a member of the `admin` role — which a fresh database does not have:

```bash
flask --app mysite users create you@example.com --active
flask --app mysite roles create admin
flask --app mysite roles add you@example.com admin
```

These are flask-security's commands, and `--active` is the one to remember:
without it the account exists and cannot sign in.

Your site tells podpack who qualifies, because podpack has no login of its
own — pass a predicate to the factory:

```python
from flask_security import current_user
import podpack

def create_app():
    return podpack.create_app(
        site_package="mysite",
        init=_wire,
        admin=lambda: current_user.is_authenticated
                      and current_user.has_role(podpack.ADMIN_ROLE),
    )
```

Leave `admin` unset and nobody qualifies: `/_status` answers 404 for
everyone, which is the right default for a route that would otherwise
publish your database identity to anyone who can reach the site.

### 9. Look at it

```bash
uv run flask --app mysite run -p 5001
```

`/` is your site, `/notes/` the installed app wearing your chrome, and
`/_status` reports where every piece of state lives.

*(Port 5001 rather than 5000: macOS gives 5000 to AirPlay Receiver, and the
symptom of the clash is 403s that look like an application bug.)*

---

## Part 2 — containerising it

The container files are already in place: `podpack substrate init` wrote them
in step 7, rendered for this site — the Containerfile's gunicorn line names
*your* factory, and the `.example` files carry your site's name and database
identity. (A site that skipped step 7 runs the same command now.) Two things
remain yours to check:

| File | Check |
| --- | --- |
| `.env.example` | ports that clash with nothing already running on this machine (`--web-port`/`--db-port` at init, or edit now) |
| `pyproject.toml` | the dependency sources — see below |

Edit the `.example` files, not `.env` and `secrets.env`: the next step creates
those from them.

### Every dependency has to come from git

A `path` source cannot work in a container. The build has no such path:

```
error: Failed to determine installation plan
  Caused by: Distribution not found at: file:///path/to/podpack
```

So for anything you intend to deploy:

```toml
[tool.uv.sources]
podpack = { git = "https://github.com/holdenweb/podpack.git" }
podpack-notes = { git = "https://github.com/holdenweb/podpack-notes.git" }
```

**Then run `uv lock`.** Editing `[tool.uv.sources]` alone is not enough — the
lockfile still carries the old source and the Containerfile builds with
`--frozen`, so the build fails with exactly the error above and no indication
that the fix was one command away.

This is what the `git` layer in the Containerfile is for: uv shells out to a
real `git` to fetch these, and the slim base image has none. A git source
resolves to what is **on the remote**, which is not necessarily what is on your
disk.

### Bring it up

```bash
./scripts/prepare-host-dirs.sh   # creates .env, secrets.env and the host directories
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
  So an improvement to a seed reaches new sites only. When podpack adds a
  line worth having — as it did for `scratch/` and `dev.db` — an existing
  site copies it by hand, and `podpack substrate diff` will not show it,
  because a seeded file is not compared. Watching podpack's own
  `src/podpack/substrate/data/gitignore` is the only way to notice.
- **Apps cannot ship migrations.** Every site installing an app regenerates that
  app's tables in its own history. Fine while a schema is stable; it is the same
  gap as the deferred app-upgrade problem.
- **Nothing checks that `SITE_NAME` in `.env` matches `name` in `app.toml`.**
  Compose cannot read TOML, which is the only reason the name is written twice.
- **The `adrs/…` links above resolve only inside podpack's repository.** Copy
  this file next to your site and they break.
