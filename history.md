# podpack: how it got here

*Written 2026-08-19, at the point where podpack first installed from PyPI.*

A companion to the other three documents, not a replacement for any of them.
[`claude.md`](claude.md) says what the project **is** today; the
[`adrs/`](adrs/) say **why** each choice was made and what the alternatives
cost; [`README.md`](README.md) says **how** to use it. This file says **how it
came to be that way**, which is the context a fresh session cannot reconstruct
from the code — including the things that were tried and abandoned, and which
of the current arrangements are load-bearing rather than incidental.

---

## 1. What podpack is, in one paragraph

A Flask framework plus a container substrate for building web sites out of
pluggable apps. **A site is a configuration file plus a list of installed
apps.** One site per running instance ([ADR-0001](adrs/0001-one-site-per-instance.md))
— a limit deliberately accepted, because it buys one `db.metadata`, one alembic
history, one app list, and `/_status` reporting one database. The goal was never
holdenweb.com; holdenweb.com is the proof that the goal was met.

## 2. The estate

| Repository | Version | Published | Role |
| --- | --- | --- | --- |
| [`podpack`](https://github.com/holdenweb/podpack) | 0.8.0 | **PyPI** | the framework and the substrate |
| [`podpack-qrcode`](https://github.com/holdenweb/podpack-qrcode) | 0.2.1 | tagged, not yet | app: QR code generator |
| [`podpack-pages`](https://github.com/holdenweb/podpack-pages) | 0.2.0 | untagged | app: HTML/Markdown content |
| [`pp-pdf`](https://github.com/holdenweb/pp-pdf) | 0.2.0 | tagged, not yet | app: PDF booklet maker and splitter |
| [`podpack-notes`](https://github.com/holdenweb/podpack-notes) | 0.1.2 | tagged, not yet | app: notes, the guide's worked example |
| `~/sites/holdenweb.com` | 1.6.0 | n/a | the real site; four apps installed |
| `~/sites/podpack-demo` | 0.2.0 | n/a | a site built *by following the guide* |

`podpack-demo` earns its place: it exists so that
[`creating-a-site.md`](creating-a-site.md) is executable rather than
aspirational. If the guide stops working, the demo stops building.

**A wrinkle worth knowing before touching tags.** `pp-pdf` was split out of
holdenweb.com and inherited its entire tag history — 29 tags from `r0.10.0` to
`r1.6.0` — while its own version is 0.2.0. Publishing triggers on `r*` tags, so
a `git push --tags` there would fire the workflow 29 times. Each run would fail
its tag-versus-`pyproject` check rather than publish anything wrong, but the
rule stands: **push named release tags, never `--tags`.**

## 3. Fifteen days, in five movements

podpack's first commit is 2026-08-05 and its 88th is 2026-08-19. The phases are
distinct enough to be worth naming, because each one ended when something was
*run* rather than when it was finished being designed.

### i. Extraction (5–7 August)

The starting point was not a framework but "the PostgreSQL container lab as
built" — infrastructure holdenweb.com already had. The framework was carved out
of it: a plugin API, an app registry, alembic driven by the app list. The first
week's decisions are the ones everything else now rests on — an app is a package
exposing `site_app` ([ADR-0002](adrs/0002-app-is-a-package-with-one-site-app.md)),
its name is its blueprint's ([ADR-0003](adrs/0003-app-name-is-blueprint-name.md)),
the app list is configuration ([ADR-0004](adrs/0004-app-list-is-configuration.md)),
and the site — not the app — decides where an app is mounted
([ADR-0006](adrs/0006-mount-points-belong-to-the-site.md)).

### ii. Framework discipline (8–11 August)

The phase that made podpack a framework rather than a generalised holdenweb.
Three commits tell the story: *Extract podpack_notes, and stop testing the
framework with a real app*; *Install no app: podpack is a framework, not a
site*; and *Make podpack's front page a fallback, not a fixture*
([ADR-0024](adrs/0024-the-front-page-belongs-to-the-site.md)). The environment
split by restore semantics ([ADR-0013](adrs/0013-environment-split-by-restore-semantics.md))
landed here too, and the ADRs themselves began.

### iii. The substrate (12–14 August)

The largest single change: the container files stopped being copied by hand and
started shipping **inside the wheel**, installed and upgraded by
`podpack substrate` ([ADR-0026](adrs/0026-the-substrate-ships-in-the-package-and-upgrades-by-manifest.md)).
Sync is three-way, with the baseline being a hash of *what podpack rendered* —
so a site edit is never clobbered; a genuine conflict writes `<file>.new` and
exits non-zero. An adversarial review of the new code found four defects on the
day it landed, which is why the commit after it is called *Fix four defects an
adversarial review found*.

Core services became compose **overlays** in the same phase
([ADR-0028](adrs/0028-core-services-are-overlays-the-site-chooses.md)), after
profiles were tried and could not work: a service outside an enabled profile is
not absent but *undefined*, so `web.depends_on: {postgres: …}` invalidates the
whole project the moment the profile is off. Overlays merge `depends_on`
additively; profiles cannot. Measured, not reasoned.

### iv. Operational hardening (15–17 August)

Everything in this phase came from something failing. `/_status` answers 404 to
non-administrators, which is indistinguishable from a missing route, so podpack
now says at boot when nobody can read it. Apps claim their tables
([ADR-0032](adrs/0032-tables-are-claimed-not-prefixed.md)) and unclaimed ones
are reported. Login moved into the core
([ADR-0033](adrs/0033-login-is-core.md)) after the third site wired the same
`Security()` by hand. Then apps gained the ability to declare what they need —
secrets and tables — with podpack refusing to boot when a declaration is unmet
([ADR-0034](adrs/0034-apps-declare-what-they-need.md)).

One commit in this phase is not about code at all: *Record what went wrong three
times today*. It is the origin of [`claude.md`](claude.md) §8.

### v. Deployment and publication (18–19 August)

The first deployment to a real Linux host, and the phase that justified all the
preceding caution. Three prerequisites surfaced in sequence, none of them
observable on macOS: the Compose v2 provider
([ADR-0016](adrs/0016-require-the-compose-v2-provider.md)), an enabled
`podman.socket`, and SELinux relabelling. Then a `%` in a generated password
killed the migration service through `configparser` interpolation, three steps
downstream of the actual cause.

Each of those became a durable fix rather than a note: SELinux is now a variable
(`VOLUME_RW`/`VOLUME_RO`) so no site hand-edits a managed file; the database URI
never passes through `configparser`; and `scripts/configure-host.py` configures
a fresh host in one command, generating secrets from `[A-Za-z0-9_-]` alone
because every other character is syntax to *something* in the chain.

The phase closed with publication: `tools/publish.py` for the laptop, and a
GitHub Actions workflow using PyPI **trusted publishing** — OIDC, so no token
exists anywhere in any repository. podpack 0.8.0 was published by that workflow
in 25 seconds.

## 4. Roads not taken

Recorded because each one looks attractive again from a distance.

| Idea | Why it went |
| --- | --- |
| **uwsgi** | replaced by gunicorn ([ADR-0019](adrs/0019-drop-uwsgi-for-gunicorn.md)); the `deploy` utility that served it went too |
| **Compose profiles for optional services** | a service outside a profile is *undefined*, breaking `depends_on` for everyone ([ADR-0028](adrs/0028-core-services-are-overlays-the-site-chooses.md)) |
| **Table-name prefixing** | a data migration for `user`/`role` on a live site; claiming is the cheaper truth ([ADR-0032](adrs/0032-tables-are-claimed-not-prefixed.md)) |
| **A flat CLI** | tried, reverted; the command groups earn their keep ([ADR-0031](adrs/0031-the-cli-keeps-its-command-groups.md)) |
| **`create_all` at boot** | gunicorn starts several workers at once, so the losers crash; a one-shot `migrate` service replaced it ([ADR-0009](adrs/0009-one-alembic-history.md)) |
| **A host PostgreSQL by default** | the container pins the version in version control ([ADR-0015](adrs/0015-postgresql-stays-in-a-container.md)) — but connecting to a host database stays available, since podpack learns about the database *only* from `SQLALCHEMY_DATABASE_URI` |
| **`HTTPContentSource`** | an abstraction with one implementation; dropped with the conversion |
| **Warning when `SITE_NAME` ≠ `name`** | considered and declined ([ADR-0023](adrs/0023-no-warning-on-name-divergence.md)) |

## 5. Facts established by measurement

The list is short because each entry cost an afternoon. None of them is
deducible from documentation.

- **`podman-compose` is not `podman compose`.** The former ignores `depends_on`
  conditions and does not interpolate the top-level `name:` key. The suite
  requires the Compose v2 provider.
- **`podman.socket` must be enabled** (`systemctl --user enable --now
  podman.socket`), with `loginctl enable-linger` for containers to survive
  logout.
- **SELinux fails misleadingly.** Without `VOLUME_RW=:Z` / `VOLUME_RO=,z`,
  PostgreSQL starts and reports *healthy* — initdb creates the cluster
  regardless — but `db-init` is unreadable, so the application role is never
  created and `migrate` is what fails.
- **`$` is unsafe in any value in either env file.** Compose expands variables
  in `.env` *and* `secrets.env`, so a password containing `$HOME` arrives as
  something else and authentication fails silently.
- **`%` was unsafe in a database password** until `alembic/env.py` stopped
  routing the URI through `configparser`.
- **podman splits `["CMD", …]` healthcheck arguments on whitespace**, so an
  inline `python -c` probe arrives mangled and reports a healthy container
  unhealthy. The healthcheck is a script file for this reason.
- **PGDATA must be a sub-directory of the mount**, because a bind mount point's
  permissions belong to the host and PostgreSQL insists on 0700
  ([ADR-0020](adrs/0020-bind-mount-ownership.md)).
- **`uv run` re-syncs from the lockfile**, silently reverting a `uv pip
  install`. Use `podpack substrate --from <artefact>` to apply a version that is
  not installed.
- **`uv lock --upgrade-package X` caches the git ref**; `--refresh-package X`
  as well. A reason to be on PyPI rather than on git sources.
- **A relative SQLite path resolves differently** for Flask-SQLAlchemy (instance
  folder) and alembic (working directory), so migrations appear to succeed while
  the site reports `no such table`. Always absolute.
- **GitHub reads a workflow from the tagged commit**, so a release tag pointing
  at a commit that predates the workflow triggers nothing at all.
- **`testpaths = ["tests"]` is required on Linux**: a bare `pytest` otherwise
  descends into `hostdata/postgres/pgdata`, which is 0700 and owned by a subuid
  the user cannot read, and collection aborts before a single test runs.

## 6. Where it stands

podpack 0.8.0 installs from PyPI and carries its substrate. All five packages
have `tools/publish.py` and a trusted-publishing workflow; all tests pass
(142 / 6 / 15 / 19 / 13). holdenweb.com is deployed and verified by a Playwright
suite that drives a real browser against the running site.

**Outstanding:**

1. **Publish the four apps.** Each needs a *pending publisher* registered on
   PyPI first — the account holder's action, not something a session can do.
2. **Retire the git sources** once they are published: delete every
   `[tool.uv.sources]` block and depend on versions. This is the payoff for
   publishing, and the last item of the publication plan.
3. **`podpack upgrade`** as one command — lock, sync, re-exec, apply the
   substrate. Deferred deliberately until PyPI made `--refresh-package` stop
   mattering.
4. **The app shipped-data gap** ([ADR-0008](adrs/0008-shipped-app-data-seeds-once.md)):
   app data seeds once, so upgrading an app never delivers changed content to a
   host that already has the directory. Known, deliberate, unsolved.
5. **Seeded substrate files never upgrade.** An improvement to `.gitignore` or
   the `.example` pair reaches new sites only, and `podpack substrate diff` will
   not show it, because a seeded file is not compared.

## 7. How to work on it

The full set is [`claude.md`](claude.md) §8. The four that matter most, and what
each one cost:

- **Verify by running.** Every worst bug in this project was found by running
  the thing, never by reading it.
- **Never `git add -A` or `git add .`** Stage named paths, and read what is
  staged before committing. A blind add put a live database password in a public
  repository.
- **A search finding nothing means suspect the pattern first.** A `grep` anchored
  at `^MAIL_PASSWORD=` reported the variable undefined; the line read
  `export MAIL_PASSWORD=`.
- **Two examples are not a convention.** A `test-` prefix was called a
  convention on the strength of two files while a third did not follow it.
