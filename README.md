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
apps = ["podpack_notes"]     # an app installed from its own repository
```

podpack itself installs no app — a repository that installed one would be a
site. For a running example see `~/sites/podpack-demo`, and
[creating-a-site.md](https://github.com/holdenweb/podpack/blob/main/creating-a-site.md) for how to build one. The other side of
the contract — writing, running, testing and shipping an app — is
[writing-an-app.md](https://github.com/holdenweb/podpack/blob/main/writing-an-app.md).

Adding an already-installed feature to a running site requires adding a line
in that file and a restart — no code change, no rebuild, and no change to
`compose.yaml`.
A rebuild is only required to install new apps.

**One site per instance.** podpack builds a single site. It does not serve
several domains from one process and there is no host-based routing; running two
sites means two deployments — same packages, different config and different
containers. That limit is deliberate, and it is what buys the simplicity
elsewhere: one `db.metadata`, one alembic history, and one app list to reason
about, rather than a registry keyed by hostname and a migration story per
tenant.

This README says how to use podpack. For **why it is the way it is** — what
forced each choice, what it cost, and what was rejected — see the
[architectural decision records](https://github.com/holdenweb/podpack/blob/main/adrs/README.md).

The container suite is arranged so that **no state and no host-specific setting
lives inside a container**: persistent state is bind-mounted from
`$HOST_DATA_DIR`, host-specific configuration read-only from `./config`, and
secrets arrive through the environment. Promotion to a real host is an edit of
`.env` alone.

## Quick start

From the root of this repository:

```bash
./scripts/prepare-host-dirs.sh && ./scripts/up.sh
```

The first creates `.env` and `secrets.env` from their examples, with working lab
values, and makes the host directories. The second always rebuilds — see
[Changing things](#changing-things) for why that is the safe default.

Then visit <http://localhost:8458/>, or check the endpoint the container
healthcheck uses:

```bash
curl -s localhost:8458/healthz
```

The richer view is `/_status`, and **the lab cannot show it to you**:

```bash
curl -s localhost:8458/_status     # 404, and that is correct
```

It answers operators only, an operator is whoever the *site's* `admin`
predicate says, and the lab's site is podpack itself — which has no login to
supply one (ADR-0025). So the lab's `/_status` is 404 for everybody,
permanently. It becomes reachable on a site that wires a login and grants
somebody the role; see [The first administrator](#the-first-administrator).

The 404 is deliberate — whether a site is a podpack site at all is not worth
publishing — but it makes a refusal and a missing route look identical, so
podpack logs the reason at boot:

```
WARNING podpack: no `admin` predicate: /_status will answer 404 to everyone.
```

That route reports the config file it read, the commit the image was built from,
every installed app with the import name it came from and its data and log
directories, and which database, role and schema it is actually connected as. If
a mount or a grant is wrong, it says so.

The import name is worth having in front of you, because it is routinely *not*
the app's own name — `podpack_notes` is what `apps` lists, and `notes` is what
keys `[site.mounts]`, `[apps.<name>]` and the directories on disk.

It also reports anything **no installed app answers for**:

```json
"unclaimed": {
  "data": ["retired_app"],
  "logs": ["retired_app"],
  "tables": ["retired_app_notes"]
}
```

Normally all three are empty. The roots hold one subdirectory per installed app
and nothing else, and every table belongs to some app. They drift legitimately,
though: removing an app from `apps` deliberately does *not* delete its data or
drop its tables, since uninstalling a feature should not destroy what it was
holding. Reported rather than removed — deleting data because a config line
changed would be the wrong instinct — so the answer to "do I have redundant
components installed?" is visible.

`tables` is read from the **database** rather than from `db.metadata`, for the
same reason the roots are read from disk: what a site declares and what it has
are different things, and the gap is the entire point. Alembic's own
`alembic_version` is excluded, belonging to the migration history rather than to
any app. A table appearing here that you did *not* expect usually means either a
retired app or an app that never declared what it owns — see `owns_tables`.

Shut down with `podman compose down`, and come back with `podman compose up -d`
— not `start`; see [Stopping and starting](#stopping-and-starting). Host storage
survives either way; see [Starting over](#starting-over).

---

# The plugin API

An app is a package exposing **one module-level `site_app`**. Everything else is
convention. This section is the reference;
[writing-an-app.md](https://github.com/holdenweb/podpack/blob/main/writing-an-app.md) is the worked guide to building one.

```python
# myapp/__init__.py
from podpack import Section, SiteApp

from .views import blueprint      # Blueprint("myapp", __name__, ...)

site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/myapp",
    nav=(Section("My App", "myapp.index"),),
)
```

| Field | Meaning |
| --- | --- |
| `blueprint` | An ordinary Flask `Blueprint`. Give it `template_folder="templates"` if it has templates. |
| `url_prefix` | Where the app *asks* to be mounted. `None` means the site root, and the site can overrule it — see below. |
| `nav` | `Section(label, endpoint)` entries contributed to the site's navigation, in installation order. |
| `init` | Optional `callable(app)`, run before the blueprint is registered, for config keys and services. |
| `owns_tables` | Table names this app claims deliberately, when its own name does not prefix them. Silences the warning below *by recording ownership*, so `/_status` can report who answers for the table. |

Two methods are optional overrides rather than fields, because an app that
wants them usually wants both and usually has state to consult:

```python
class Notes(SiteApp):
    def healthz(self):
        return Health(ok=store.reachable(), detail="mongodb unreachable")

    def status(self):
        return {"queued": store.pending()}

site_app = Notes(blueprint=blueprint, url_prefix="/notes")
```

| Method | What podpack does with it |
| --- | --- |
| `healthz()` | Called on every `/healthz`. Returns `Health(ok, detail, fatal)`, or `None` for "not reported" — which is *not* the same as healthy. |
| `status()` | Called on every `/_status`. Returns a mapping, reported under that app's `reported` key. |

Both are called in an app context, both may do I/O, and podpack reports how
long `healthz()` took so a slow check is visible rather than mysterious.
**An exception in either is caught and reported**, never propagated: a
health check is the last thing that should be able to take a site down, and
a diagnostic that needs diagnosing is worth nothing.

**A failing app does not by itself make the site unhealthy.** `/healthz`
stays 200 and names the app, because the container healthcheck gates the
whole stack on that answer — one broken feature must not stop a site
serving the rest. An app whose absence genuinely leaves the site pointless
says `fatal=True` and gets a 503.

Install it by adding its **import name** to `apps` in the site's config file.
Apps are installed in the order listed: nav entries appear in that order, and an
app's `init` may rely on a service an earlier one registered.

### Installing an app, and enabling one

These are two different operations, and only the second is free.

**Enabling** an app already present in the image is a line in `app.toml` and a
restart — no code change, no rebuild, no compose change. That is the claim this
framework is built around.

**Installing** one that is not yet in the image means putting the distribution
there, which is a dependency change and a rebuild:

```bash
uv add "pp-pdf @ git+https://github.com/…/pp-pdf"   # records it in uv.lock
podman compose up -d --build                        # bakes it into the image
```

...and *then* the line in `app.toml`. podpack itself is indifferent to how the
distribution arrived — `apps = ["pp_pdf"]` is an import name, and the registry
only does `import_module`. An index, a git repository, a direct URL and a local
path are all the same to it.

Only one of those sources asks for a *tool* the image would not otherwise have:
**git**, because uv shells out to it. That is why the build stage installs one —
see [The image](#the-image). A local path needs the source inside the build
context, which a bind mount does not provide.

Index and URL installs need no extra tool, but that is not the same as needing
nothing: the builder is `python:3.12-slim` and has no C compiler, so a
dependency that resolves to an sdist needing compilation fails there whatever
its source. Wheels are fine; anything that has to be built is not, until a
toolchain is added.

### What a site wires for itself

Some things belong to the site rather than to any one feature — mail, login,
session policy. They are not apps, so they do not go in the `apps` list: pass a
`callable(app)` as `init` instead.

```python
# holdenweb/__init__.py -- what gunicorn is pointed at
import podpack

def create_app():
    return podpack.create_app(site_package="holdenweb", init=_wire)

def _wire(app):
    # `app_config` needs an app context and podpack pushes none before calling
    # init; and with no request to resolve an app from, the name is required.
    with app.app_context():
        settings = podpack.app_config("mail")      # from [apps.mail] or your own table
    app.config.update(MAIL_SERVER=settings["server"])
    mail.init_app(app)
    security.init_app(app, user_datastore)
```

It runs **after** the site's config is loaded and **before** the apps are
installed, so an app's own `init` can rely on a service the site registered.
Without the `app_context()` the site does not boot — it fails with
`RuntimeError: Working outside of application context.`, and an app's own
`SiteApp.init` is subject to exactly the same rule.

The reason these are not apps is worth knowing, because writing shims to make
them look like apps is a natural first thought: `flask-mailman` and
`flask-paranoid` register no blueprint at all, and `flask-security` brings its
own, created inside `init_app`. A `SiteApp` is built around a blueprint, so a
shim would mean inventing one — and then inheriting a template namespace, a data
directory and a log directory that nothing uses. See
[ADR-0025](https://github.com/holdenweb/podpack/blob/main/adrs/0025-the-site-wires-its-own-extensions.md).

### The app's name is its blueprint's name

`site_app.name` is derived, not declared, and it identifies the app everywhere it
needs identifying: **its template namespace, its data and log directories, and
its section of the site's config file.** So name the blueprint carefully — that
is the decision.

It reads from the blueprint because that name is already the app's public
identity: it prefixes every endpoint, and so appears in every `url_for` and every
nav entry. It is also what podpack resolves an app from during a request, through
`request.blueprint`.

A separate `name` field would be a copy of that, and a copy can drift. When there
were two, nothing detected them disagreeing — the registry created and chowned
one directory while the views read and wrote another, and `app_config()` quietly
returned an empty dict, with nothing raised at boot or in the request.

### Name the distribution `podpack-<app name>`

A **convention, not a mechanism.** An app's distribution should be called
`podpack-` plus the app's own name — `podpack-notes`, `podpack-pdf` — in the way
`pytest-*` and `flask-*` packages are. It makes an app findable on an index and
tells a reader at a glance what a package is for.

podpack does *not* discover apps by scanning for that prefix, and the reasons are
worth recording so the idea does not get reinvented:

- **It would not remove anything.** Scanning finds what is installed; it does not
  install it. The distribution is only present because the lockfile put it there,
  so its dependency entry is needed either way. What a scan would replace is the
  `apps` list — the other list.
- **And the `apps` list is the part doing the work.** It decides what is
  *enabled*, without a rebuild, and in what order. Discovery-by-presence means
  in-the-image equals switched-on, so turning a feature off becomes a rebuild,
  and ordering — which nav and `init` both depend on — is gone.
- **It would fail quietly where it is needed most.** An editable install, which
  is how you work on an app locally, need not register the module names such a
  scan reads. Discovery would work in the built image and find nothing on the
  bench.

If the app list ever does become a chore, the answer is **entry points**, not a
name prefix: they impose no naming, work for a distribution called anything, and
`pp-pdf` already ships one. That is the hybrid worth building — entry points for
discovery, the config list for ordering and enablement.

### Where an app lands is the site's decision

The app list decides *whether* a feature is installed. The shape of the address
space stays the site's, so `url_prefix` is a request rather than a claim, and a
site overrules it in a table of its own:

```toml
[site.mounts]
myapp = "/tools/myapp"
```

It lives under `[site]` rather than in `[apps.myapp]` because it is **site policy
and not app configuration**: the app takes no part in the decision, and so never
sees it — `app_config()` returns only what the app itself is meant to read. Two
consequences worth knowing:

- **The key is the app's name, which is its blueprint's name**, and that is not
  always the import name in `apps`. `podpack_notes` is imported; it answers to
  `notes`.
- **Naming an app that is not installed is a boot failure.** Keeping mounts in
  their own table means the two can drift, and a stray entry would otherwise be
  silent — leaving the app at the address it asked for, which is exactly the
  address the site said it did not want.

Only apps being moved need an entry, so the table doubles as the site's map of
everywhere it has chosen to put something.

Nothing else needs saying — not by the app, and not by the site. **A `Section`
names an endpoint, not a path**, so the navigation resolves through `url_for`
as the chrome renders and follows the app wherever it ends up. That is also why
an entry naming an endpoint no view provides is a boot failure: a bad one would
break `url_for` in the chrome and take out every page on the site, not just the
page it points at, so it is worth refusing to start over.

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

**Prefix `__tablename__` with the app's name.** Table names are the one
identifier podpack does *not* namespace — templates, data and log directories
and config sections all carry it, `db.metadata` is one flat namespace shared by
every installed app. podpack warns as it installs an app whose table names its
own name does not prefix, and refuses to boot a site where two apps claim the
same one, naming both. `/_status` reports which app owns which table.

## Data and logs

Every installed app gets a subdirectory of the host-mounted roots, named after
the app:

```
<data root>/<name>/     persistent data the app owns
<log root>/<name>/      logs it writes
```

Uninstalling an app leaves its directories alone, so nothing is lost by taking a
feature out of the app list and putting it back. `/_status` lists what is left
behind under `unclaimed`, so retired data stays visible instead of merely
present.

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

**Generating** a revision happens on the host, because the result is a file that
belongs in the repository. Autogenerate compares the models against a live
database, so this is the one job that wants the database port — which the
suite does not publish by default (see
[ADR-0027](https://github.com/holdenweb/podpack/blob/main/adrs/0027-the-database-port-is-published-only-on-request.md)).
Ask for it, for as long as it takes:

```bash
podman compose --profile dbport up -d dbport
```

```bash
export PODPACK_CONFIG=config/app.toml
export SQLALCHEMY_DATABASE_URI=postgresql+psycopg2://holdenweb_app:…@127.0.0.1:5433/holdenweb
uv run alembic revision --autogenerate -m "what changed"
```

```bash
podman compose --profile dbport rm -sf dbport
```

`POSTGRES_HOST_PORT=5439 podman compose --profile dbport up -d dbport` picks a
different number for one use, since a shell variable beats the one in `.env`.

**Applying** one happens in the container, automatically at startup, or by hand:

```bash
podman compose run --rm migrate alembic upgrade head
podman compose run --rm migrate alembic current
```

Do not reach for `podman compose run --rm migrate alembic revision
--autogenerate`. It fails, and twice over: `/app/alembic/versions` is root-owned
while the image runs as uid 10001, so alembic does the whole comparison and then
dies on the final write with `PermissionError`; and even given permission, the
file would be destroyed with the `--rm` container instead of landing in the
repository. The image's code being read-only to the process running it is the
right arrangement — generating revisions is simply not a container's job.

Because a revision's directory does not say which app it came from, its
**message should**. See
`alembic/versions/205fc0d0ce92_notes_app_initial_schema.py`.

Building that metadata deliberately does *not* construct a Flask app. The
factory needs a secret key and a database URI before it will run, and coupling
migrations to it would make a broken factory a broken migration too.

It does hold every app to the plugin contract even so, and the reason is the
ordering rather than anything about migrations: `migrate` gates `web` with
`service_completed_successfully`. While a module with no `site_app` was accepted
here, that gate passed and the site's real failure surfaced in `web` — one
service after the cause, so the logs blamed whatever came next. Checking costs
no Flask app.

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

## Two ways to run a site

The same site, the same `config/app.toml`, the same app list and the same
alembic history — run two ways:

```bash
./scripts/up.sh     # containers: podman compose, gunicorn, the real shape
./scripts/dev.sh    # local: no containers at all, Flask's dev server
```

`dev.sh` reads `dev.env` (made from `dev.env.example` on first run, and
gitignored), applies the migration history, and starts the development
server. It expects **a natively installed PostgreSQL** — not SQLite, and
that is the one place this costs you a setup step:

```bash
createuser --pwprompt mysite_app
createdb --owner mysite_app mysite
```

podpack will not run those for you. The server is yours, and a run script
that issued DDL against it would be doing something you had not asked for —
so `dev.sh` checks, and prints exactly those two lines when it cannot
connect.

**Why not SQLite, when it needs no setup at all?** Because
`alembic revision --autogenerate` run against SQLite produces revisions
PostgreSQL may reject. This project has the scar: a baseline written that
way used `ALTER COLUMN`, which is PostgreSQL-only syntax and fatal on
SQLite, and the mismatch surfaced only when a fresh container database was
built months later. Authoring against the engine you deploy on is the whole
point of authoring on the host
([ADR-0011](https://github.com/holdenweb/podpack/blob/main/adrs/0011-revisions-authored-on-the-host.md)).
SQLite remains right for *tests*, which create their schema directly and
author nothing.

What the local mode does not give you: gunicorn, a proxy, the image, and any
backing service beyond the PostgreSQL you installed — so a site running
mongodb has none locally unless it installs one, and `dev.sh` says so.

## Core services

A site chooses its backing stores. `compose.yaml` is the base — the site, its
storage and the migration gate — and every store is an overlay beside it,
named in `COMPOSE_FILE`:

```
COMPOSE_FILE=compose.yaml:compose.postgres.yaml
```

| Service | What it is |
| --- | --- |
| `postgres` | **Required.** The SQL database `db`, the alembic history and the site's login tables all live in it, and `create_app` refuses to start without a `SQLALCHEMY_DATABASE_URI`. |
| `mongodb` | Optional. For apps that store documents rather than rows. |

What is optional about PostgreSQL is the *container*, not the database: a
site may drop `compose.postgres.yaml` from `COMPOSE_FILE` by hand and point
the URI at a managed instance, which is the Opalstack arrangement
[ADR-0015](https://github.com/holdenweb/podpack/blob/main/adrs/0015-postgresql-stays-in-a-container.md) anticipated.

```bash
uv run podpack substrate services                 # what this site runs
uv run podpack substrate services --add mongodb   # enable another
```

`--add` records the service, rewrites `COMPOSE_FILE`, and delivers its
variables to `.env` and its secrets to `secrets.env.example` by the ordinary
append rule. Three things stay yours, because podpack writes neither
credentials nor host state: the entries in `secrets.env`, a run of
`./scripts/prepare-host-dirs.sh`, and `podman compose down && ./scripts/up.sh`.

**Choosing services is the site owner's job, taken independently of the
installed apps.** An app cannot declare that it needs one — that would be a
dependency-management problem where a sentence in a README will do. And
there is no `--remove`: taking a store away is a decision about data rather
than configuration, so edit `COMPOSE_FILE` yourself and nothing podpack owns
will have destroyed anything.

Each service publishes no host port, and brings its own on-request forwarder
under a profile of its own name (see [Ports](#ports)):

```bash
podman compose --profile mongodb-port up -d mongodb-port
MONGODB_HOST_PORT=27020 podman compose --profile mongodb-port up -d mongodb-port
```

**SQL is the one store an app may assume.** `db`, its single metadata and its
one alembic history are core, so the alembic environment is in the base
rather than in postgres's overlay — what is optional is the *server*, and a
site may point `SQLALCHEMY_DATABASE_URI` at a managed PostgreSQL and run no
container at all. A site running no SQL keeps every other part of podpack
and has a `migrate` service with nothing to do.

Why overlays and not compose profiles, since profiles are what they look
like they are for: a service outside an enabled profile is not absent but
*undefined*, so `web.depends_on: {postgres: …}` invalidates the entire
project the moment the profile is off. Overlays merge `depends_on`
additively, which is how the ordering guarantees survive being optional.
Measured, and recorded in
[ADR-0028](https://github.com/holdenweb/podpack/blob/main/adrs/0028-core-services-are-overlays-the-site-chooses.md).

## The first administrator

`/_status` answers a member of the `admin` role and nobody else, and a fresh
database has neither the role nor a user — so a new site's operator view is
unreachable until three commands have been run. They are flask-security's
own, not podpack's:

```bash
flask --app mysite users create you@example.com --active
flask --app mysite roles create admin
flask --app mysite roles add you@example.com admin
```

The password is prompted for, hidden and confirmed. **`--active` matters**:
without it the account is created and cannot sign in, which looks exactly
like a wrong password.

Login itself is podpack's, not yours to wire —
[ADR-0033](https://github.com/holdenweb/podpack/blob/main/adrs/0033-login-is-core.md).
`podpack.auth` ships the `User` and `Role` models, the datastore and the
`is_admin` predicate `/_status` asks, and `create_app` installs them. A site
writes nothing: no `models.py`, no `Security()`, no predicate. Mail and
session policy remain the site's, under
[ADR-0025](https://github.com/holdenweb/podpack/blob/main/adrs/0025-the-site-wires-its-own-extensions.md).

The three commands above are still flask-security's rather than podpack's,
because they exist, validate the identity through the registration form and
resolve users the way the rest of flask-security does; a framework copy would
only be a worse one.

**podpack says so at boot** when the role does not exist yet:

```
WARNING podpack: no 'admin' role exists, so /_status will answer 404 to
everyone -- including you. Create it and grant it: ...
```

That exists because the endpoint refuses with 404 rather than 403 on purpose,
which makes a refusal and a missing route identical from outside. It cost an
afternoon once.

A site with an unusual idea of who counts as an operator can still pass its
own `create_app(admin=…)`; the default is a default, not a fixture.

## Getting it, and keeping it current

The substrate ships inside the podpack package, and a site installs it with
one command rather than by copying files out of this repository:

```bash
uv run podpack substrate init      # lay it down, or adopt a hand-copied set
uv run podpack substrate status    # how every file relates to the installed podpack
uv run podpack substrate upgrade   # bring the copy forward after upgrading podpack
uv run podpack substrate diff      # what exactly differs, per file
```

`init` derives the site's package from `pyproject.toml` (override with
`--site-package` and friends), renders the one parameterised line — the
Containerfile's gunicorn factory — and records what it wrote in
`substrate.json`, which the site commits. Run on a site that already copied
the substrate by hand, it adopts in place: identical files baseline
silently, edited ones are kept and reported.

`upgrade` is a three-way comparison per managed file, against the recorded
baseline of **what podpack rendered**: files you have not touched take
upstream fixes; files you edited are kept, and said so; a file changed on
both sides gets podpack's version written *beside* it as `<file>.new` and an
exit status of 1 — resolve each with `--take-upstream PATH` or `--keep
PATH`. `--take-upstream` also discards an edit you made earlier, which is how
a site adopted with local differences converges; either way your version is
kept as `<file>.orig`, because **nothing is ever clobbered**. `status
--check` exits 1 if an upgrade would act, which is the CI hook; a damaged
`substrate.json` exits 2, so the two are never confused.

A file that resolves **outside the site** — because you pointed it, or the
directory holding it, at a shared checkout — is reported as `not managed
here` and left entirely alone, in every command. That is a settled fact
rather than pending work, so it does not hold `--check` red for ever.

Configuration is different, by design: once delivered, `.env.example`,
`secrets.env.example` and a live `.env` change **only by the addition of new
parameters** — an upgrade appends variables this site has never been given
(each offered exactly once, so deleting one is respected) and never rewrites
a line. The live `secrets.env` is never written at all: a newly-required
secret is reported for you to add by hand, because an appended lab default
in that file would be a weak credential on its way to production.

Out of the command's reach, always: `config/app.toml`, `alembic/versions/`,
`pyproject.toml`, the lockfile, your source, and anything in `scripts/` it
did not put there — though the seeded `.gitignore` suggests `scratch/` for
your own experiments and one-off utilities, so that `scripts/` can hold
only the two files podpack manages. See
[ADR-0026](https://github.com/holdenweb/podpack/blob/main/adrs/0026-the-substrate-ships-in-the-package-and-upgrades-by-manifest.md)
for the full rules and what was rejected.

This repository's own root is a rendered instance of the packaged substrate
— podpack is its own first consumer — and a test pins the two byte-identical.

## Ports

| Service | Host port | Notes |
| --- | --- | --- |
| Flask | `127.0.0.1:8458` | 8456 is the real site's local port; 8457 is the MongoDB lab |
| PostgreSQL | none | published only on request — see below |

The web port is offset on purpose: a lab that silently binds the production
port is a lab that will one day be mistaken for production. It binds to
loopback only, so it is not reachable from the network; change it in `.env`
if it clashes.

**The database publishes nothing.** Nothing inside the suite needs it —
the app and the migration service reach `postgres:5432` across the compose
network — so the only beneficiary was the host, at the cost of a number every
other deployment on the machine had to avoid. Two sites are otherwise
isolated by `SITE_NAME` alone. Ask when you want it:

```bash
podman compose --profile dbport up -d dbport
```

```bash
POSTGRES_HOST_PORT=5439 podman compose --profile dbport up -d dbport
```

A shell variable beats `.env`, so the second form chooses a number for one
use without editing a committed file. `podman compose --profile dbport rm -sf
dbport` takes it away again. See
[ADR-0027](https://github.com/holdenweb/podpack/blob/main/adrs/0027-the-database-port-is-published-only-on-request.md).

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
| Per-host wiring | `.env` | environment variables |
| Credentials | `secrets.env` | environment variables |

`HOST_DATA_DIR` and `HOST_LOG_DIR` default to `./hostdata` and `./hostlogs`
(both gitignored) so the suite is self-contained. On a real host they become
absolute — `/srv/holdenweb/data`, `/var/log/holdenweb` — and nothing else needs
to change.

Apps live under an `apps/` level rather than beside `postgres/` so that the two
ownership fixes cannot reach each other: a single recursive chown of the data
root would take the database's data directory with it.

### Why there are two environment files

They are split by **what restoring them means**, not by secrecy:

| | `.env` | `secrets.env` |
| --- | --- | --- |
| Contains | paths, ports, site name, worker count | credentials, `SECRET_KEY`, database identity |
| On a new host | **edit it** — that is what it is for | **put it back verbatim** |
| If it changes | nothing is lost | sessions void, or the site cannot reach its own data |

Mixing them is what made restoring a manual step: the backup had to be
hand-edited before it could be used, in exactly the procedure that should have
none. A restore is now *copy `secrets.env`, edit `.env`* — and the file you must
not touch is the one you never open.

Only `.env` is read for variable substitution, so `compose.yaml` never refers to
a credential and stays safe to commit and to read. `podman compose config` is the
exception worth knowing: it expands `env_file` contents into the environment it
prints, so treat its output as being as sensitive as `secrets.env` itself.

### The site names its own containers

`SITE_NAME` in `.env` gives the compose project and the image their names:

```console
$ podman ps --format '{{.Names}}'
holdenweb-lab-postgres-1
holdenweb-lab-web-1
```

So two sites on one host cannot collide, and `podman ps` says which is which
instead of showing two identically-named sets. A second site needs distinct
**ports** as well — `WEB_HOST_PORT` and `POSTGRES_HOST_PORT` are per-deployment,
and a clash fails at bind time with `address already in use`.

Keep `SITE_NAME` in step with `name` in `config/app.toml`. Compose cannot read
TOML, which is the only reason the site's name is written twice.

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

**When in doubt, rebuild.** `src/` is baked into the image, so editing framework
code and then reaching for `restart` brings back the *previous* build and leaves
the site behaving like the last commit — a confusing symptom with an unrelated
cause. Rebuilding unconditionally costs about six seconds when nothing has
changed, because layers are content-addressed and an untouched file invalidates
nothing:

```bash
./scripts/up.sh          # always rebuilds, and stamps the commit into the image
```

That is the safe default. The narrower loops are worth knowing because they are
faster and because they are what a real host does:

```bash
podman compose restart web       # after editing config/app.toml
podman compose restart postgres  # after editing config/postgresql.conf

# after editing config/pg_hba.conf only -- no restart needed.
# `-u postgres` is required: pg_ctl refuses to run as root.
podman compose exec -u postgres postgres pg_ctl reload

podman compose up -d             # after editing .env (recreates containers)
```

Editing a mounted config file needs no rebuild and no image change, which is
exactly the behaviour you want on a real host. `pg_hba.conf` is the one that can
be applied without even a restart. Anything under `src/`, `alembic/` or the
`Containerfile` needs a build.

### Which commit is actually running

`scripts/up.sh` stamps the commit into the image. Read it from the container,
which works on any site including this lab:

```console
$ podman compose exec web printenv PODPACK_BUILD_COMMIT
a7cf297-dirty
```

`/_status` reports the same value under `build_commit`, on a site whose
`admin` predicate answers for you — not on the lab, which has no login and so
no operator.

Compare it with `git rev-parse --short HEAD` and the question "is the container
running the code I am looking at?" has an exact answer rather than an inference
from timestamps. A `-dirty` suffix means the image was built from an uncommitted
tree, which is normal while working and worth noticing when it is not. Building
by hand instead reports `unknown`.

### Stopping and starting

These are two pairs, and mixing them is the easy mistake:

```bash
podman compose stop     # containers keep existing, merely stopped
podman compose start    # ...so they can be started again

podman compose down     # containers are REMOVED (network too)
podman compose up -d    # ...so coming back has to recreate them
```

`start` only starts containers that already exist. After a `down` there are
none, and it fails with `service "init-storage" has no container to start` —
which reads like a fault in the one-shot service but is only saying the
container is gone. `up -d` is always safe: it creates whatever is missing and
starts the rest.

Both routes leave host storage alone, so no data is lost either way.

On the way up, either command honours the `depends_on` gates — `init-storage`
and `migrate` run again before `web`. That is safe by design: the chown is
idempotent and `alembic upgrade head` has nothing to do when the schema is
already current.

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

The shortest route needs no port and no local `psql`:

```bash
podman compose exec postgres psql -U labadmin -d holdenweb
```

To use the host's own `psql` — or any other client — publish the port first
(`podman compose --profile dbport up -d dbport`, see [Ports](#ports)):

```bash
PGPASSWORD=holdenweb-app-password psql -h 127.0.0.1 -p 5433 -U holdenweb_app -d holdenweb
```

`holdenweb_app` is the *application* role: it can log in, connect to one
database, and owns one schema. The superuser credentials in `secrets.env` are
used exactly once, by the bootstrap below, and are never given to the app.

## First-run bootstrap

[`db-init/01-create-app-user.sh`](https://github.com/holdenweb/podpack/blob/main/db-init/01-create-app-user.sh) creates the
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

The web healthcheck runs [`container/healthcheck.py`](https://github.com/holdenweb/podpack/blob/main/container/healthcheck.py)
as a **script file**, not a `python -c` one-liner: podman splits `["CMD", ...]`
healthcheck arguments on whitespace, so an inline probe arrives mangled and dies
with a SyntaxError — reporting the container unhealthy however well it is
actually running.

## The image

[`Containerfile`](https://github.com/holdenweb/podpack/blob/main/Containerfile) builds in **two stages**, because three things
are needed to build the virtual environment and none of them to run it:

| Left behind in the builder | Why it is there | Weight |
| --- | --- | --- |
| `git` | uv shells out to it for a dependency locked to a git source | 104 MB |
| the `uv` binary | resolves and installs from the lockfile | 47 MB |
| uv's download cache | populated as a side effect of `uv sync` | ~44 MB |

No dependency is locked to a git source **yet**, so git is currently groundwork
rather than load-bearing: the build would succeed without it today. It is
installed ahead of need because the first app installed straight from a
repository would otherwise fail the build with "Git executable not found",
which names nothing that would lead you here.

Together that is roughly half the image: **398 MB single-stage against 203 MB**.
The runtime stage copies the finished `.venv`, the source, the migration
environment and the healthcheck, and nothing else.

Note that *removing* git in a later layer would not have worked. The layer that
installed it still carries the files, and a deletion only adds another layer on
top — the image gets slightly bigger, not smaller. Not shipping it is the only
way to not ship it.

### Both stages must use the same `WORKDIR`

A venv is tied to its absolute path twice over. Console-script shebangs carry the
interpreter path, and the project is installed into it as an **editable**
pointing at `<workdir>/src` — which is also why the runtime stage copies the
source: the venv alone is not a complete installation.

So a venv built under one directory and copied to another is thoroughly broken,
not subtly so. Built under `/build` and copied to `/app`:

```console
$ gunicorn --version
sh: 1: gunicorn: not found          # exit 127 — reads like a PATH problem
$ python -c "import podpack"
ModuleNotFoundError: No module named 'podpack'
```

Neither message mentions the venv, which is what makes it worth knowing. It is
the same trap as renaming the project directory on the host, where `uv sync`
will not repair it either because it audits packages rather than scripts. There
the fix is `rm -rf .venv && uv sync --all-groups`; here it is keeping the two
`WORKDIR` lines identical.

In this file a mismatch mostly fails loudly instead: `COPY --from=builder
/app/.venv` cannot find its source and the build stops. Only changing both paths
to *different* values produces the broken image above.

## Deploying to Opalstack

Opalstack's AlmaLinux 9 servers run rootless podman, so this suite deploys there
essentially as it stands. The mapping:

| Opalstack gives you | goes in |
| --- | --- |
| an **Nginx Proxy Port** app's port assignment | `WEB_HOST_PORT` in `.env` |
| the app directory `~/apps/<name>/` | `HOST_DATA_DIR`, `HOST_LOG_DIR` in `.env` |
| the site domain | `base_url` in `config/app.toml` |

That is the whole of it, which is the point: the port a managed host allocates is
exactly the kind of per-host fact `.env` exists for. Opalstack generates the
nginx upstream to proxy your site to that port, so nothing above the container
needs to know it.

Two things to watch, neither of them podpack's doing:

- **`podman-compose` is what their tutorial uses, and it will not honour this
  suite's ordering.** See [Compose front-ends](#compose-front-ends): the
  `depends_on` gates are load-bearing here and it ignores them. Use `podman
  compose` with the Compose v2 provider, or sequence the phases by hand.
- **`loginctl enable-linger <uid>`** is needed for containers to keep running
  when you are not logged in; their tutorial mentions it in passing.

`base_url` is the site's public URL — `https://example.com`, with **no port**.
The allocated port is where the container listens, not how the world addresses
the site, and the two are only ever the same number in a lab.

### Using the managed PostgreSQL instead

Opalstack provides a managed PostgreSQL 17, the same version this suite runs in a
container. Swapping to it is deliberately small, because podpack learns about the
database *only* from `SQLALCHEMY_DATABASE_URI`: drop the `postgres` service, its
two `init-storage` mounts and the `db-init/` mount, and repoint the URI. No
application code and no migration changes.

Worth knowing what it costs, though. A container pins `postgres:17` per
deployment and upgrades when you decide; the managed instance is the server's,
shared with everything else on it, and moves when the host moves. Keeping the
container is the same instinct as mounting the config read-only — the version
belongs to version control rather than to the machine.

## Running on Linux

Two differences on a real Linux host:

- **SELinux (RHEL, Fedora, CentOS Stream).** Append `,Z` to the read-write bind
  mounts and `,z` to the read-only ones in [compose.yaml](https://github.com/holdenweb/podpack/blob/main/compose.yaml) — e.g.
  `${HOST_DATA_DIR}/postgres:/var/lib/postgresql/data:Z`. Without a label,
  SELinux denies the container access. The mount points are commented in the
  file.
- **Ownership.** `init-storage` handles it, but if you prefer to pre-create the
  directories yourself, `prepare-host-dirs.sh` does the equivalent
  `podman unshare chown` on Linux.

## Compose front-ends

**They are not interchangeable, and this suite needs `podman compose`.**

- `podman compose` — delegates to Docker Compose v2, which honours `depends_on`
  conditions. **Required**, because every ordering guarantee here rests on them.
- `podman-compose` — starts the containers but **silently ignores
  `depends_on` conditions.** It also names containers with underscores
  (`holdenweb-lab-pg_web_1`) rather than hyphens, so never point the two
  front-ends at the same project without taking the stack down first.

The difference is not theoretical. The same file, one service sleeping five
seconds and a second gated on its completion:

```console
podman-compose:   ONCE-START 626   AFTER-START 626   ONCE-END 631   # gate ignored
podman compose:   ONCE-START 633   ONCE-END 638      AFTER-START 638  # gate honoured
```

Under `podman-compose` three guarantees quietly disappear: `init-storage` no
longer precedes the servers, so the bind-mount ownership problem returns; `web`
no longer waits for `migrate`, so the site can start against a schema that has
not been created; and it no longer waits for PostgreSQL to be accepting
connections. Nothing reports any of this — the stack simply comes up, and works
or does not depending on timing.

An earlier version of this file said `podman-compose` "also works". It was
inherited from the original lab and had never been tested.

## Development

```bash
uv sync
uv run pytest
uv run mypy
```

The tests cover what the registry promises — that the app list is configuration
rather than code, that models reach `db.metadata`, that template namespacing and
site override both work, that data seeds once and re-arms on deletion, and that
the migration environment needs no Flask app.

`mypy` is a dependency rather than something to remember, because annotations
nobody checks are comments that look authoritative. It reads its settings from
`pyproject.toml` and covers `src/` and `tests/` both. Two suppressions exist and
both say why in place: `db.Model`, which flask-sqlalchemy builds at runtime, and
one deliberate `SiteApp(name=...)` in a test that asserts the call is an error.

There is a MongoDB sibling of this substrate, near-identical in shape and on
different ports so the two can run side by side. It stayed in the holdenweb.com
working tree when this project was extracted.
