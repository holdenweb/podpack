Here's a description of a site creation and management framework, and the local directory contains its current implementation.
I'd like you to hjelp me move this project forwards in a controlled way.



# Project brief: a system for building and running web sites



*Written 2026-08-05 to bring a fresh Claude Code session up to speed. Paste it,

or point at it, at the start of a session.* Amended slightly by Steve in a manual

edit to dictate goals and guardrails.



---



## 1. The goal



**Build a system that lets Steve create and manage web sites easily and

efficiently.** Not a single site — a repeatable way to stand one up, add

functionality to it, and run it.



`holdenweb.com` is the first site and, for now, doubles as the reference

implementation: whatever the system turns out to be, this site should be

expressible in it.



**The immediate next step** is to make functionality pluggable: a way to add a

group of endpoints — a blueprint, with its own templates, static files, models

and configuration — so that adding a feature to a site is as close as possible

to *adding a Django app to `INSTALLED_APPS`*.



---



## 2. Current state of the code



Flask, Python ≥3.12, `src/` layout, packaged with `uv_build`, dependencies

managed by `uv` (`uv.lock` is committed). Everything below is in this repo

unless stated.



### The application factory



[`src/holdenweb/__init__.py`](src/holdenweb/__init__.py) is the whole

application — 319 lines holding the factory, the models, and most of the views.



- `create_app(config_overrides=None)` at line 231. Reads config from the

  environment, applies overrides, calls `init_app` on each extension, then

  registers blueprints.

- Extensions (`mail`, `paranoid`, `db`, `security`) are created **unbound** at

  module level (lines 70-73) and attached inside the factory, so multiple app

  instances can coexist — tests rely on this.

- `app = create_app()` and `application = app.wsgi_app` at module level (lines

  315-316), because [`wsgi.py`](wsgi.py) imports them for uwsgi.



### Blueprints today



| Blueprint | Defined | Registered | Prefix |

| --- | --- | --- | --- |

| `main_bp` | line 88 of `__init__.py` | line 309 | none |

| `pdf_blueprint` | [`src/holdenweb/pdf.py:17`](src/holdenweb/pdf.py) | line 310 | `/pdf/` |



`pdf_blueprint` is the closest thing to a prototype "app" — but it is **not

self-contained**: it renders `markdown.html` from the *main package's*

`templates/`, and imports its forms from `holdenweb.forms`. Closing that gap is

essentially the next step (§4).



The intention is to allow automated discovery of all assets necessary (files,

database tables. mongdb collections) to extend the functionality of the base site

byscanning a specific portion of the configuration data detailing which additions

are required. The "additions" will presumably need to conform to a standard API,

which should initially be documented in the README.



### Models and migrations



- `Role` and `User` (lines 78-84) via `flask_security`'s `fsqla_v3` mixins.

  That is the entire schema at present.

- Migrations are alembic. **Critical detail for §4:**

  [`alembic/env.py`](alembic/env.py) does `from holdenweb import db` (line 11)

  and `target_metadata = db.metadata` (line 28). Autogenerate therefore only

  sees models that have been imported by the time `env.py` runs.

- `env.py` reads `SQLALCHEMY_DATABASE_URI` from the environment, so alembic and

  the running app can never disagree about which database they mean.



### Content



[`src/holdenweb/content.py`](src/holdenweb/content.py) defines a

`ContentSource` Protocol (`html`, `markdown`, `asset`) with two

implementations:



- `LocalContentSource` — reads `src/holdenweb/data/{html,md}-pages/`. The

  default; no extra services in dev.

- `HTTPContentSource` — fetches from `$CONTENT_BASE_URL` with an

  `Authorization: Bearer $CONTENT_ACCESS_TOKEN` header. Enabled when both vars

  are set; the app refuses to boot with only one.



The chosen source is stashed at `app.extensions["content"]` (lines 291-306) and

looked up by views via `current_app`. **This is the existing precedent for a

per-app service registry** and worth reusing in §4.



Assets inside pages are rewritten server-side to `/asset/...` and proxied by

Flask, so browsers never talk to the content host directly and it can stay

behind a shared secret. See the "Content topology" section of

[README.md](README.md).



However, there seems at present to be little justification for this complication,

so until there is a specific need for it the HTTPContentSource should be mothballed.



### Everything else



- **Navigation** is a hardcoded `SECTIONS` list (lines 60-64). A pluggable app

  will want to contribute entries to it — a real design point, not a detail.

  Therefore the app loading process should be capable of integration into its

  containing site's navigation.

- **Templates/static** live in `src/holdenweb/templates/` and

  `src/holdenweb/static/`, both package-level. Clearly apps must be capable

  of using their own private templates, so the template-location process

  should adapt to prefer app-private templates as default, though allowing

  the site to provide overrides.

- **Tests**: `tests/conftest.py`, `test_routes.py`, `test_content.py`. pytest,

  with `responses` for HTTP stubbing and `playwright` available.

- **Config** comes entirely from environment variables; see

  [.env.example](.env.example) for the full set. `.env` is gitignored.

- **Serving**: uwsgi via `pyuwsgi`. [`uwsgi.ini`](uwsgi.ini) is the production

  file and is currently hardwired to Opalstack paths

  (`/home/sholden/apps/second-alma/`). Those paths should instead be relative to

  another configuration parameter, whose value the `deploy` utility should

  play a part in establishing (perhaps in the form of an alication name) to allow

  sites to be easily deployed as Opalstack apps on demand.

- **Database in production**: PostgreSQL. The root

  [`docker-compose.yml`](docker-compose.yml) reaches the *host's* Postgres via

  `host.docker.internal`, with an `extra_hosts` alias so the same file works on

  Docker Desktop, Linux Docker and Podman.

  If there's an outstanding migration in alembic that should be committed to give

  a baseline database definitition for all sites.



---



## 3. The container substrate



Two container labs were built (2026-08-04) to allow production-shaped code to

be tested locally. Each runs a Flask app plus a database, with **no state and no

host-specific setting inside any container**:



- persistent state → bind-mounted from `$HOST_DATA_DIR`

- host-specific config → bind-mounted read-only from `./config`

- secrets and wiring → environment, via `.env`



so promotion to a real host is an edit of `.env` alone.



**Where they are now:** the PostgreSQL lab (`podman2/`) **has been moved into

its own project** and is no longer in this repo. The MongoDB lab is still at

[`podman/`](podman/), untracked. Whether it should also move, or be deleted, is

an open housekeeping question.



Both are working and verified: clean-slate build, data surviving container

destruction, bootstrap not re-running, host-side DB access, config edits taking

effect on restart with no rebuild.



### Gotchas already paid for — do not rediscover these



1. **Bind-mount ownership.** Servers drop to unprivileged uids (999 for both

   `postgres` and `mongod`, 10001 for the app image) and cannot write to host

   directories they do not own — `mongod` crash-loops on its logfile. Fixed by

   an `init-storage` service: a throwaway root container that chowns the mounts

   before anything else starts, gated by

   `depends_on: {condition: service_completed_successfully}`. Works on both

   macOS virtiofs and rootless Linux.

2. **PostgreSQL demands mode 0700 on `PGDATA`**, and a bind mount point's

   permissions belong to the host (world-writable on macOS virtiofs). So the

   host directory mounts at `/var/lib/postgresql/data` and `PGDATA` points one

   level deeper at `.../data/pgdata`, which `initdb` creates itself. Never

   pre-create that sub-directory.

3. **Podman splits `["CMD", ...]` healthcheck arguments on whitespace**, so an

   inline `python -c "..."` probe arrives mangled and dies with a SyntaxError —

   the container reports unhealthy however well it is running. Use a script

   file (`["CMD", "python", "/app/healthcheck.py"]`). This one only shows up

   under the Compose v2 provider, not `podman-compose`.

4. **A PostgreSQL config file outside the data directory means initdb's

   generated one is ignored entirely** — so `hba_file` and `ident_file` must be

   named explicitly, and all three files mounted together. Upside:

   `ALTER SYSTEM` fails by design, keeping config in version control.

5. **`pg_isready` needs `-U` and `-d`** in the healthcheck, or it reports the

   server ready before the bootstrap has created the application's database.

6. **`pg_ctl reload` must run as `-u postgres`**; it refuses to run as root.

7. **Ports on this machine.** 8456 is the real site's local port *and* a

   long-running `holdenwebcom-web-1` container; 5432 is a native `postgres`;

   27017 is a native `mongod`. The labs deliberately use 8457/27018 (Mongo) and

   8458/5433 (Postgres).

8. **Bootstrap scripts in `/docker-entrypoint-initdb.d` only run while the data

   directory is empty** — and since it is on the host, that means "first time

   on this machine", not "each time the container is recreated". Re-arming

   means deleting the host data directory.



### A decision taken but not implemented



Publishing the *database* port to the host was discussed and judged worth

dropping: deleting the `ports:` block from the database service is the entire

change, since container-to-container traffic is unaffected by publishing, the

app URIs already use internal ports, and the healthchecks run inside the

containers. Host access would then be via `podman compose exec`, a throwaway

container joined to the compose network, or an on-demand socat forwarder under

a Compose profile. **The benefit is fidelity and removing a wrong-database

footgun, not security** — the ports are already bound to loopback only.



---



## 4. The next step: pluggable "apps"



**The ask:** add a group of endpoints, in a blueprint, in a way that makes

installing a feature into a site feel like adding a Django app.



### What a Django app bundles, and what Flask gives us free



| Django app provides | Flask equivalent | Status |

| --- | --- | --- |

| views + URLconf | `Blueprint` + `url_prefix` | free |

| templates | `Blueprint(template_folder=...)` | free, but see collisions below |

| static files | `Blueprint(static_folder=...)` | free |

| management commands | `blueprint.cli` | free |

| signals / startup hooks | `record_once`, `before_app_request` | free |

| **models** | — | **missing** |

| **migrations** | — | **missing** |

| **`INSTALLED_APPS`** | — | **missing** |

| **app registry / discovery** | — | **missing** |

| **admin registration** | — | **missing** |

| **nav contribution** | — | **missing** (`SECTIONS` is hardcoded) |



So the work is not "how do I make a blueprint" — it is the four or five things

Django bundles *around* the blueprint.



### The specific obstacles in this codebase



1. **Migrations are the hard part.** `alembic/env.py` reads `db.metadata` after

   importing `holdenweb`. A pluggable app's models are invisible to autogenerate

   unless something imports them first. Two credible routes:

   - *Single migration history*: the registry imports every installed app's

     models before alembic runs. Simple; but apps cannot be installed and

     migrated independently.

   - *Per-app history*: alembic's `version_locations` plus `branch_labels`,

     giving each app its own migration directory and branch — the true Django

     analogue. More moving parts, and multiple heads to reason about.

2. **Template namespacing.** Flask searches blueprint template folders *after*

   the app's, with no automatic namespacing — two apps shipping `index.html`

   collide silently. Needs a convention: `templates/<appname>/page.html`.

3. **`pdf_blueprint` is the test case.** Making it self-contained — its own

   templates, its own forms — is the smallest honest proof that the mechanism

   works. If the PDF tools can be lifted out and reinstalled unchanged, the

   design holds.

4. **Nav and chrome.** `SECTIONS` needs to become something apps contribute to,

   or sites will keep needing hand-edits in the core package.

5. **Config.** Each app may want its own keys and defaults. The existing

   pattern — read env in the factory, stash the built object in

   `app.extensions[...]` — is the obvious thing to generalise.



### The discovery question



How does a site declare which apps it has?



- **Explicit list in config** — the literal `INSTALLED_APPS` analogue. Simple,

  ordered, obvious.

- **Python entry points** (`[project.entry-points."holdenweb.apps"]`) — a site

  becomes a dependency list; `uv add` installs a feature. This fits the

  existing `uv` + `uv_build` packaging unusually well, and fits the stated goal

  ("create and manage web sites easily and efficiently") better than a list.

- **Directory scan** — implicit and fragile; mentioned only to dismiss.



A hybrid is plausible: entry points for discovery, explicit config for ordering

and enablement.



### Open questions to settle early



- **One site or many?** "Web sites", plural. Does the system serve several

  sites from one running instance (host-based routing — note `SERVER_NAME` is

  already handled conditionally at line 242), or generate/deploy a separate

  instance per site (separate container stack, same packages, different

  config)? The container work so far implies the latter. **This changes the

  design more than anything else on this page.**

- **Is content a plugin?** The `ContentSource` abstraction already makes

  content pluggable in one dimension. Is "the content-driven site" just the

  first app, or is it core?

- **How much of `__init__.py` moves?** It is currently factory + models + views

  in one file. Extracting the factory into its own module, with `main_bp`

  becoming just another installed app, is the natural shape — but it is a

  bigger change than the first plugin.

- **Does the platform become its own package**, with `holdenweb.com` as a

  consumer of it? The container project has already split out this way.



---



## 5. Working preferences observed



Derived from the 2026-08-04/05 sessions; correct me if any of it is wrong.



- **Verify by running.** Every claim about the container labs was checked

  against a live stack, and that is what caught the ownership, `PGDATA` and

  healthcheck bugs. Do not report something as working on the strength of the

  code reading alone.

- **Comments explain *why*, not *what*.** The existing code does this

  consistently — e.g. the note at lines 68-69 on unbound extensions, and the

  `SERVER_NAME` comment at 240-241. Match that density.

- **Config belongs to the host and to version control**, not to whoever last

  had a superuser session — the reasoning behind read-only mounted config and

  the deliberate breaking of `ALTER SYSTEM`.

- **Secrets in the environment; everything else in files.** Consistently

  applied across the site and both container labs.

- **Flag concerns, then finish the job.** Prefer being told about a problem

  alongside completed work, rather than being asked about it first.

