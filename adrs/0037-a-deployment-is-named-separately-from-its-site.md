# ADR-0037: A deployment is named separately from its site

**Status:** Proposed

**Date:** 2026-08-24

## Context

Eleven names are in play across a podpack site, for four things. The four are
**the site** (its code and content), **a deployment** of it on a host, **that
deployment's public address**, and **its database**. Everything else is derived
or incidental.

| Name | On holdenweb.com | Lives in | Varies by |
| --- | --- | --- | --- |
| `site_package` | `holdenweb` | `pyproject.toml`, `create_app` | site |
| distribution | `holdenweb` | `pyproject.toml` | site |
| `[site] name` | `holdenweb.com` | `config/app.toml`, in git | site |
| `SITE_NAME` | `holdenweb-com` | `.env` | **deployment** |
| compose project | `holdenweb-com` | derived from `SITE_NAME` | **deployment** |
| image tag | `…-com-web:latest` | derived from `SITE_NAME` | **deployment** |
| `BACKUP_ROOT` | `~/backups/holdenweb-com` | derived from `SITE_NAME` | **deployment** |
| `POSTGRES_DB` | `holdenweb_com` | `secrets.env` | **deployment** |
| public hostname | `os.holdenweb.com` | **nowhere** | **deployment** |
| `base_url` | unset | **`config/app.toml`, in git** | **deployment** |
| checkout directory | `holdenweb-com` | the filesystem | incidental |

Two rows are wrong, and it is the same error twice: **a value that varies per
deployment kept somewhere that cannot vary.**

**`SITE_NAME` is not the site's name.** It names the compose project, the image
and the backup root — all per-deployment — while `[site] name` in `app.toml` is
the site's own label, committed, identical everywhere. Two "site names"
differing by a single character. podpack's own help text has been accurate
throughout: `--site-name` is documented as *"compose project/image name"*. The
concept was written down correctly beside the wrong word for it.

**`base_url` is in the wrong file.** It is read from `host_config["site"]`, which
is `config/app.toml` — in git, so the same in every deployment. But production
answers on `holdenweb.com` and staging on `os.holdenweb.com`. ADR-0036 put
`PODPACK_PROXY_HOPS` in `.env` for exactly this reason and left `base_url`
where it was.

The consequence is not theoretical. A rebuild script written for this site had
to invent a `SITE_HOST` variable of its own, because podpack has no name for
the address a deployment answers on.

### The measurement that decides the mechanism

Compose validates its own project-name variable and refuses what podpack's
`name:` key silently accepts:

| How the project name is set | Given `holdenweb.com` |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | **refused** — *invalid project name … must consist only of lowercase alphanumeric characters, hyphens, and underscores* |
| `name: ${SITE_NAME}` | accepted, exit 0, silently becomes `holdenwebcom` |

Every naming confusion of the week descends from that silent strip:
`holdenweb.com` against `holdenweb-com` against `holdenwebcom`; a
`podman compose exec` reporting *"service web is not running"* about a container
that plainly was; and a claim, since withdrawn, that two checkouts had collided.
**The validation is the feature.**

### The delivery problem underneath

`.env.example` is a *seeded* substrate file (ADR-0026): delivered once, never
upgraded. So `PODPACK_PROXY_HOPS` — whose entire documentation lives there —
can never reach a site that already exists. holdenweb.com upgraded through
0.9.0b2 reporting every file `ok` and remained silently short of the one thing
that release was for.

It shows in the file itself. This site's `.env` carries 42 comment lines; the
current `.env.example` carries 81. The live file is documented to an older
standard, and nothing can bring it forward.

## Decision

**1. Two names for a deployment, not one, and neither is "site".**
`DEPLOYMENT_NAME` is the deployment's identity — the backup root and the image
tag key on it. `COMPOSE_PROJECT_NAME` is compose's own variable, read natively,
with `compose.yaml`'s `name:` line deleted so that compose validates it rather
than normalising it in silence.

They will hold the same value in every deployment anyone has yet built, and are
kept in step by the configurator rather than by the operator. They are separate
because nothing guarantees they must agree, and two names that coincide are
cheaper to keep aligned than one name to split later.

**2. `[site] name` becomes `[site] title`.** It is a human label for chrome, in
git, the same everywhere — which is what it has always been. A rename rather
than a removal: `core.py` reads it by direct index for the home page's title, so
deleting the key raises `KeyError` on a request rather than degrading.

**3. `PUBLIC_URL` and `PUBLIC_PORT` join `.env`.** `base_url` reads from the
environment rather than from `app.toml`. Both are **required to be present** and
**default to something safe** — `http://localhost` and empty — so a site can
never be silently without them, and a lab still starts with nothing configured.
Where `PUBLIC_PORT` is not empty it is appended after a colon.

`PUBLIC_PORT` is *not* `WEB_HOST_PORT`. The latter is what the container
publishes on; the former is what a visitor types. In a lab they coincide;
behind a proxy they are unrelated and `PUBLIC_PORT` is empty, 443 being implicit
in the scheme. Anything that defaults one from the other must say it is
guessing.

**4. The configurator owns the variable set; `.env.example` retires.** Every
variable's name, meaning, default and scope lives in `configure-host.py`, which
is a managed file and therefore deliverable. A new variable then reaches every
existing site through `substrate upgrade` plus one run, instead of never.

The configurator writes `.env` with its prose, not only its assignments: the
comments are most of what a reader needs, and `fill()`'s docstring already says
so. And it **preserves keys it does not know about** — a hand-added variable is
reported so that its presence is visible, and kept. An unrecognised key is a
remark, never an error.

**5. A site still setting only `SITE_NAME` is told, at every boot.** The rename
cannot be delivered by an upgrade, because it is a change to a file the site
owns. A site that misses it would otherwise answer quietly to a compose project
called `podpack`, which is the default.

## Consequences

`restore.sh` stops restoring `.env`. ADR-0036's reasoning applies to the whole
file, not one variable: `secrets.env` is the site's identity and must come back
verbatim; `.env` is the host's and must not. That closes the fault where a
restore silently handed a checkout the backup's compose project, and removes the
per-host patching a rebuild script currently does afterwards.

`configure-host.py`'s refusal to overwrite splits. Refusing to regenerate
`secrets.env` is right — a fresh salt makes every stored password unverifiable.
Refusing to edit `.env` was only ever collateral, and `.env` holds no secret.

**The bug that started this becomes preventable rather than merely fixed.** A
configurator that asks *"is anything proxying to this site?"* and *"what address
do visitors use?"* would have caught the `http://` reset links outright, and
catches them on every future rebuild. Of everything this work produced, an
editor that asks is the only mechanism that addresses the original fault rather
than its symptom.

Against that: every existing deployment must edit `.env` by hand, once. There
are three, all on one host, and the boot-time check above is what makes a missed
one loud. The cost is paid once and is the last time it need be — after this,
`.env` is generated rather than seeded, and podpack can deliver a new variable
to a site that already exists.

Two names to keep in step is a standing cost, accepted deliberately. The
alternative — one name, split when a case appears — trades a small permanent
cost for a migration exactly like this one.
