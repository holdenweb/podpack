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

### 7. Create the schema

An app's tables are the *site's* to migrate — one history for the whole site,
per [ADR-0009](adrs/0009-one-alembic-history.md) — so alembic lives here, not in
the app.

```bash
uv run alembic init alembic
```

`alembic init` generates `alembic/env.py` with `target_metadata = None` about
two thirds down. Three changes to that file, shown in place:

```python
import os                                   # add, with the other imports
from logging.config import fileConfig
...
config = context.config                     # already there -- do not add it again

if os.environ.get("SQLALCHEMY_DATABASE_URI"):        # add these two lines
    config.set_main_option("sqlalchemy.url", os.environ["SQLALCHEMY_DATABASE_URI"])
...
from podpack.migrations import target_metadata as _tm   # replaces
target_metadata = _tm()                                 #   target_metadata = None
```

Then in `alembic.ini`, delete the one uncommented `sqlalchemy.url = ...` line.
The URI is a secret and belongs in the environment, where the running site reads
it too — so the two can never disagree about which database they mean.

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

### 8. Look at it

```bash
uv run flask --app mysite run -p 5001
```

`/` is your site, `/notes/` the installed app wearing your chrome, and
`/_status` reports where every piece of state lives.

*(Port 5001 rather than 5000: macOS gives 5000 to AirPlay Receiver, and the
symptom of the clash is 403s that look like an application bug.)*

---

## Part 2 — containerising it

podpack's own repository *is* the substrate. A site copies it:

```bash
cp -r /path/to/podpack/{container,db-init,scripts} .
cp /path/to/podpack/{Containerfile,compose.yaml,.dockerignore,.env.example,secrets.env.example} .
cp /path/to/podpack/config/{postgresql,pg_hba,pg_ident}.conf config/
```

Then:

| File | Change |
| --- | --- |
| `Containerfile` | `'podpack:create_app()'` → `'mysite:create_app()'`; drop the `COPY README.md` line unless you have one |
| `.env.example` | `SITE_NAME`, and ports that clash with nothing already running |
| `secrets.env.example` | `POSTGRES_DB`, `POSTGRES_APP_USER`, and the URI to match |
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

- **A site copies the substrate.** Eight files and three directories, and
  nothing keeps a site's copy in step with podpack's afterwards. A `podpack
  init` command would fix that; there isn't one.
- **Apps cannot ship migrations.** Every site installing an app regenerates that
  app's tables in its own history. Fine while a schema is stable; it is the same
  gap as the deferred app-upgrade problem.
- **Nothing checks that `SITE_NAME` in `.env` matches `name` in `app.toml`.**
  Compose cannot read TOML, which is the only reason the name is written twice.
- **The `adrs/…` links above resolve only inside podpack's repository.** Copy
  this file next to your site and they break.
