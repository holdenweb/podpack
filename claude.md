Here's a description of a site creation and management framework. This directory
contains the framework itself; two sibling projects consume it.
I'd like you to help me move this project forwards in a controlled way.

# Project brief: podpack, a system for building and running web sites

*Written 2026-08-05 to bring a fresh Claude Code session up to speed. Amended by
Steve to dictate goals and guardrails. Rewritten 2026-08-06, after the framework
was built, to describe what now exists rather than what was planned.*

---

## 1. The goal

**Build a system that lets Steve create and manage web sites easily and
efficiently.** Not a single site — a repeatable way to stand one up, add
functionality to it, and run it.

`holdenweb.com` is the first site and doubles as the reference implementation:
whatever the system turns out to be, that site should be expressible in it.

**Single sites only.** podpack builds one site per running instance. It does not
serve several domains from one process and there is no host-based routing;
running two sites means two deployments — same packages, different config and
containers. This settles what an earlier draft of this brief called the question
that changes the design more than anything else, and it is what justifies one
`db.metadata`, one alembic history and one app list rather than registries keyed
by hostname. Do not add `SERVER_NAME` handling or a site registry.

---

## 2. Where the work lives

| Directory | What it is | State |
| --- | --- | --- |
| `~/sites/podpack` | **this repo** — the framework, plus the container substrate that runs it | 13 commits, working |
| `~/sites/pp-pdf` | the PDF tools as a standalone installable package | 20 commits; **reconciled** — installs as a podpack app and still works as a plain blueprint (§5) |
| `~/sites/holdenweb.com` | the original Flask site | untouched; **not yet adapted** — see §6 |

Guardrail carried over from the earlier sessions and still in force: **do not
change anything outside the current working directory without asking first.**
Reading is fine.

---

## 3. podpack: what now exists

**A site is a config file plus a list of installed apps.**

```toml
[site]
name = "example.com"
apps = ["podpack_notes"]
```

Adding a feature to a running site is a line in that file and a restart — no
code change, no rebuild, no change to `compose.yaml`. The full plugin API is
documented in [README.md](README.md); this is the shape of it.

### The contract

An app is a package exposing **one module-level `site_app`**:

```python
from podpack import Section, SiteApp
from .views import blueprint      # Blueprint("myapp", __name__, ...)

site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/myapp",       # a request; the site may mount it elsewhere
    nav=(Section("My App", "myapp.index"),),   # endpoint, not path
    init=None,                 # optional callable(app) for config and services
)
```

**The app's name is its blueprint's name** — `site_app.name` is derived, not
declared. It is the template namespace, the data and log directory names, and
the config section, so naming the blueprint is the decision that matters. It
reads from the blueprint because that name is already the app's public identity
(it prefixes every endpoint, and so every `url_for` and nav entry) and is what
podpack resolves an app from at runtime via `request.blueprint`. A second
declared copy could only drift, and when there were two, nothing detected them
disagreeing: the registry prepared one directory while the views used another and
`app_config()` returned `{}`, silently.

Everything else is convention:

- **`models.py`** — the registry imports it at install time. Defining a
  `db.Model` subclass registers it on `db.metadata` as an import side effect,
  which is the whole of model registration and the reason migrations can see an
  app the migration environment has never heard of.
- **`templates/<name>/`** — namespaced so two apps cannot collide. Search order
  is **site → app → podpack defaults**; Flask already searches the application's
  templates before any blueprint's, and podpack appends its own loader last, so
  an app extending `base.html` renders correctly on a site with no chrome of its
  own.
- **`data/`** — shipped data, seeded to the host on install *only if the target
  is empty*: the same "first time on this machine" rule as `db-init/`. The app
  then reads the host copy, so editing it on the host takes effect with no
  rebuild.
- **`[apps.<name>]`** in the config file — the app's own config namespace, read
  with `podpack.app_config()`.
- **`<data root>/<name>/` and `<log root>/<name>/`** — per-app directories
  created at startup. The compose file mounts the *roots*, which is what keeps
  installing an app out of it. File logging is attached to the app's package
  logger, so `logging.getLogger(__name__)` writes to `<name>.log` for free.

### Migrations

One alembic history. `podpack.migrations.target_metadata()` imports the
installed apps' models and returns `db.metadata` — deliberately **without**
building a Flask app, so a broken factory is not also a broken migration. A test
deletes `SECRET_KEY` and `SQLALCHEMY_DATABASE_URI` to keep that honest.

A one-shot `migrate` compose service runs `alembic upgrade head` before `web`,
gated by `service_completed_successfully`. That also retires the create_all race:
gunicorn starts several workers at once and the losers crashed on tables a
sibling had just made.

**The footgun, verified rather than assumed:** autogenerate sees exactly the apps
that are enabled, so running it with an app disabled really does propose
`op.drop_table(...)` for that app's tables. Always autogenerate against the full
app list. Per-app histories (`version_locations` plus `branch_labels`) are the
answer if this ever gets painful enough to justify multiple heads.

### Tests

`uv run pytest` — 13 tests covering what the registry promises: the app list
being configuration rather than code, models reaching `db.metadata`, template
namespacing and site override, seeding once and re-arming, and the migration
environment needing no Flask app.

---

## 4. The container substrate

No state and no host-specific setting lives inside a container: persistent state
bind-mounted from `$HOST_DATA_DIR`, host config read-only from `./config`,
secrets through the environment. Promotion to a real host is an edit of `.env`.

`init-storage` → `postgres` (waits healthy) → `migrate` → `web`.

The image builds in two stages. `git` is installed at build time — uv shells out
to it for a dependency locked to a git source, which is how an app not published
to an index gets installed — but neither it nor uv nor uv's cache is needed to
run the site. Leaving all three in the builder halves the image, 398MB to 203MB.
Nothing is locked to a git source yet, so that layer is groundwork rather than
load-bearing; it is there so the first such app fails on nothing.

Adding an app from outside the repo is therefore two operations, not one:
`uv add` plus a rebuild puts the distribution *in the image*, and a line in
`app.toml` plus a restart *enables* it on the site. Only the second is the
config-and-restart the framework advertises.

Verified on a clean slate: host storage deleted, cluster re-initialised,
bootstrap re-run, and the migration creating the schema on PostgreSQL before the
site came up.

### Gotchas already paid for — do not rediscover these

1. **Bind-mount ownership.** Servers drop to unprivileged uids (999 for
   `postgres`, 10001 for the app) and cannot write to host directories they do
   not own. Fixed by `init-storage`: a throwaway root container that chowns the
   mounts before anything else starts, gated by
   `depends_on: {condition: service_completed_successfully}`. Works on macOS
   virtiofs and rootless Linux.
2. **PostgreSQL demands mode 0700 on `PGDATA`**, and a bind mount point's
   permissions belong to the host (world-writable on macOS virtiofs). So the
   host directory mounts at `/var/lib/postgresql/data` and `PGDATA` points one
   level deeper at `.../data/pgdata`, which `initdb` creates itself. Never
   pre-create that sub-directory.
3. **Podman splits `["CMD", ...]` healthcheck arguments on whitespace**, so an
   inline `python -c "..."` probe arrives mangled and dies with a SyntaxError.
   Use a script file — `container/healthcheck.py`. Only shows up under the
   Compose v2 provider, not `podman-compose`.
4. **A PostgreSQL config file outside the data directory means initdb's
   generated one is ignored entirely** — so `hba_file` and `ident_file` must be
   named explicitly, and all three files mounted together. Upside:
   `ALTER SYSTEM` fails by design, keeping config in version control.
5. **`pg_isready` needs `-U` and `-d`** in the healthcheck, or it reports the
   server ready before the bootstrap has created the application's database.
6. **`pg_ctl reload` must run as `-u postgres`**; it refuses to run as root.
7. **Ports on this machine.** 8456 is the real site's local port; 5432 is a
   native `postgres`; 27017 is a native `mongod`. This suite uses 8458/5433, and
   the MongoDB lab 8457/27018.
8. **Bootstrap scripts in `/docker-entrypoint-initdb.d` only run while the data
   directory is empty** — and since it is on the host, that means "first time on
   this machine", not "each time the container is recreated". Re-arming means
   deleting the host data directory. App data seeding follows the same rule.
9. **Apps live under an `apps/` level**, not beside `postgres/`, so the two
   chowns cannot reach each other. A single recursive chown of the data root
   would take the database's data directory with it and undo gotcha 2.
10. **Do not give SQLAlchemy pool options it did not ask for.** SQLite's
    StaticPool rejects `pool_size` outright, so podpack passes through only the
    keys the site actually set. Supplying defaults made it unable to run on
    SQLite at all.
11. **A venv is tied to its absolute path, twice over.** Console-script shebangs
    carry the interpreter path, and the project is installed into it as an
    editable pointing at `<workdir>/src`. So a moved venv gives both
    `gunicorn: not found` (exit 127, which reads like a PATH problem) *and*
    `ModuleNotFoundError`. `uv sync` will not repair it, because it audits
    packages rather than scripts. Two faces of the same trap:
    - on the host, renaming the project directory — `rm -rf .venv && uv sync
      --all-groups`. This bit when `podman/` became `podpack/`.
    - in the image, the two build stages must share a `WORKDIR`. Mostly this
      fails loudly, because `COPY --from=builder /app/.venv` cannot find its
      source; only setting both to *different* values yields the broken image.
12. **Do not try to delete something out of an earlier image layer.** The layer
    that added it still carries the files and the deletion only adds another on
    top, so the image grows. Not shipping it — a second stage — is the only way
    to not ship it.
13. **`.dockerignore` patterns anchor to the context root.** A bare
    `__pycache__/` excludes nothing under `src/`; it needs `**/__pycache__`.
    Written the wrong way the file looks right, does nothing, and the image goes
    on shipping bytecode compiled on the laptop — including valid `.pyc` for
    migrations deleted long ago, which are loaded in preference to compiling the
    source actually present. Verify an ignore file by building and looking.
14. **Revisions are generated on the host, never in the container.**
    `/app/alembic/versions` is root-owned while the image runs as uid 10001, so
    `alembic revision --autogenerate` there does the whole comparison and dies
    on the final write; and `--rm` would discard the file anyway. Code being
    read-only to the process running it is correct — applying migrations is a
    container's job, authoring them is not.

### Still open here

- The MongoDB lab is still untracked inside the `holdenweb.com` working tree.
  Move it or delete it.
- `config/app.toml` declares `base_url` and nothing reads it. Under single-site
  that is a meaningful setting (canonical URL for mail and feeds) — so wire it
  up or drop it.

---

## 5. `pp-pdf`: the first external plugin — reconciled

`~/sites/pp-pdf` (formerly `~/sites/hwpdf`) holds the PDF booklet and
page-splitting tools. It was built against the *speculative* discovery design in
an earlier draft of this brief rather than the registry that actually got built,
so the two did not fit together. They now do, along the lines this section
originally recommended: a `site_app` alongside the existing `pdf_blueprint`
export, with the entry point kept.

| | `pp-pdf` as a plain blueprint | `pp-pdf` under podpack |
| --- | --- | --- |
| Discovery | entry point `[project.entry-points."holdenweb.apps"]` | import name `pp_pdf` listed in `apps` |
| What is discovered | a bare `Blueprint` | `site_app: SiteApp` |
| Mount point | the host's argument to `register_blueprint` | `[apps.pp_pdf] url_prefix`, defaulting to the app's own |
| Base template | its own `pp_pdf/standalone.html` | the site's `base.html` |
| Registration hook | `blueprint.record_once` | `SiteApp.init` |
| Nav, models, data, per-app dirs | none | supported by the registry |

Both halves work and are tested independently; podpack is an optional import in
that package, guarded with `find_spec`, so it stays usable by a site that has
never heard of this framework. That is a genuine virtue of how it was built and
was deliberately not thrown away.

Two things changed on **this** side to meet it:

- `Section` holds an **endpoint name**, not a path, so nav resolves through
  `url_for` and follows an app wherever it is mounted. An entry naming an
  endpoint no view provides is now a boot failure, because a bad one breaks the
  chrome on every page rather than 404ing on one.
- `SiteApp.url_prefix` became a request rather than a claim: a site overrules it
  with `url_prefix` in the app's own config section. The app list decides
  *whether* a feature is installed; the shape of the address space stays the
  site's.

Where the two contracts already agreed, and it was worth keeping: **both
namespace templates under the app's own name, and both rely on Flask searching
the application's templates before any blueprint's so the site can override by
shadowing.**

Growing entry-point discovery in podpack — the hybrid the earlier draft
described, entry points for discovery and the config list for ordering and
enablement — remains the alternative to the config list. It is more work and
should wait until there are enough apps for the list to feel like a chore.

---

## 6. `holdenweb.com`: not yet adapted

Untouched by this work. Flask, Python ≥3.12, `src/` layout, `uv_build`, alembic
baseline migration already committed (`ddec9f8`).
`src/holdenweb/__init__.py` is still factory + models + views in one 319-line
file, with a hardcoded `SECTIONS` list and blueprints registered by hand.

Adapting it means becoming a podpack site: `create_app(site_package="holdenweb")`,
its `SECTIONS` becoming nav contributed by apps, its templates staying as the
site layer that overrides everything below.

### Guardrails still outstanding from the original brief

- **The largest part of the adaptation is configuration.** holdenweb.com reads
  *everything* from the environment; podpack expects non-secret settings in a
  host-mounted TOML and secrets in the environment.
- **`HTTPContentSource` should be mothballed** until there is a specific need —
  Steve chose deletion. **But `/asset/` and `rewrite_asset_urls` must stay.**
  They are not HTTP-specific, and `data/html-pages/writing/images/` holds 12
  images that three pages reference relatively; deleting the route 404s them.
- **`uwsgi.ini` is hardwired to Opalstack paths**
  (`/home/sholden/apps/second-alma/`). Those should be relative to a
  configuration parameter — perhaps an application name — that a `deploy`
  utility helps establish, so sites can be deployed as Opalstack apps on demand.
- **Is content core or a plugin?** Still open. Easier to answer once `hwpdf` is
  installed and there are two apps to compare.

---

## 7. What's next

1. **Adapt `holdenweb.com`** to be a podpack site (§6), starting with config.
   This is the milestone; everything else here is small.
2. Housekeeping: the MongoDB lab, `base_url`.
3. **Decide about `pp-pdf`'s two discovery routes.** It exposes both a
   `holdenweb.apps` entry point resolving to a bare blueprint and a `site_app`
   for podpack. Both work and are tested; the entry point is what keeps the
   package usable by a site that has never heard of this framework. Keep both,
   or drop one, but do it deliberately rather than letting the entry point rot.

Not on this list any more: reconciling the PDF tools, which is done (§5).

---

## 8. Working preferences

- **Verify by running.** Every claim about the container work was checked
  against a live stack, and that is what caught the ownership, `PGDATA`,
  healthcheck and SQLite-pooling bugs. Do not report something as working on the
  strength of the code reading alone. Where a test asserts a framework
  guarantee, check it fails when the mechanism is disabled — a test that cannot
  fail is not evidence.
- **Run the commands you document.** The README carries about twenty of them,
  and they are as much a claim as anything in the code. `alembic revision
  --autogenerate` sat there for weeks as the documented way to make a migration
  and could never have worked — the only commands anyone had actually run were
  the read-only ones beside it. A documented command that has not been executed
  since it was written is an untested assertion.
- **Comments explain *why*, not *what*.** The code does this consistently — the
  note on unbound extensions, on why `PGDATA` is a sub-directory, on why engine
  options are not defaulted. Match that density.
- **Config belongs to the host and to version control**, not to whoever last had
  a superuser session — the reasoning behind read-only mounted config and the
  deliberate breaking of `ALTER SYSTEM`.
- **Secrets in the environment; everything else in files.**
- **Flag concerns, then finish the job.** Prefer being told about a problem
  alongside completed work, rather than being asked about it first.
