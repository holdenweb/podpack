# Architectural decision records

Why podpack is the way it is. Each record states the pressure that forced a
choice, what we decided, what it cost, and what else was on the table — that last
section being the one that stops a decision getting reproposed in six months.

These were written on 2026-08-08, reconstructed from the commits that carry the
reasoning. The decisions themselves were taken over the preceding days, so a
record's **Date** is when it was written, not when the choice was made.

**A record is immutable once accepted.** When a decision changes, add a new one
that supersedes it and mark the old `Superseded by ADR-nnnn`. Do not rewrite an
existing record: the reasoning that was true at the time is the thing of value,
and a record edited into agreement with the present teaches nobody anything.

Start from [the template](0000-template.md), which also sets out what belongs
here as against in `README.md` or `claude.md`.

## Scope

| | |
| --- | --- |
| [ADR-0001](0001-one-site-per-instance.md) | **One site per running instance.** No multi-domain serving, no host-based routing. Two sites means two deployments — and that is what buys one `db.metadata`, one migration history and one app list. |

## The plugin contract

| | |
| --- | --- |
| [ADR-0002](0002-app-is-a-package-with-one-site-app.md) | **An app is a package exposing one `site_app`.** Everything else is convention. The registry supplies what Flask does not: models migrations can see, a declaration of what is installed, per-app state, nav. |
| [ADR-0003](0003-app-name-is-blueprint-name.md) | **An app's name is its blueprint's name.** Derived, not declared — two fields that had to agree disagreed silently, and the framework was policing the invariant from inside its own apps. |
| [ADR-0004](0004-app-list-is-configuration.md) | **The installed-app list is configuration.** A line in the site's TOML and a restart. Discovery by distribution-name prefix was rejected; entry points remain the deferred alternative. |
| [ADR-0005](0005-template-resolution-order.md) | **Templates resolve site → app → framework.** Site override is free, because the site *is* the Flask application; podpack's own loader goes last. |
| [ADR-0006](0006-mount-points-belong-to-the-site.md) | **Mount points belong to the site.** An app's `url_prefix` is a request; `[site.mounts]` overrules it. It left the app's config namespace because an app was being handed a decision it takes no part in. |
| [ADR-0022](0022-nav-is-contributed-and-addressed-by-endpoint.md) | **Nav is contributed by apps, addressed by endpoint.** A bad entry is a boot failure, because the chrome resolves nav on every page — so one broken link breaks the whole site, not one page. |
| [ADR-0023](0023-no-warning-on-name-divergence.md) | **No warning when a blueprint's name differs from its module's.** The divergence is deliberate and useful; the mistake worth catching is already caught by Flask. Report the mapping instead of policing it. |
| [ADR-0024](0024-the-front-page-belongs-to-the-site.md) | **The front page belongs to the site.** podpack's `/` is a fallback, registered only if no app claims it — it used to be a fixture, and won silently against any site that wanted its own front page. |
| [ADR-0025](0025-the-site-wires-its-own-extensions.md) | **The site wires its own extensions.** `create_app(init=…)` for mail and session policy — which are not apps, because they register no blueprint. Its login clause is superseded by ADR-0033. |
| [ADR-0033](0033-login-is-core.md) | **Login is core.** podpack ships the models, the datastore and `is_admin`, because it already named the role, guarded `/_status` with it and documented three commands for it — leaving each site the same twenty-four lines. ADR-0029's argument one layer up: what the framework's own endpoints require is not optional. |

## App state and data

| | |
| --- | --- |
| [ADR-0007](0007-per-app-data-and-log-directories.md) | **Per-app directories under mounted roots.** Compose mounts the roots, so installing an app never touches the compose file. |
| [ADR-0008](0008-shipped-app-data-seeds-once.md) | **Shipped app data seeds once.** The same "first time on this machine" rule as the database bootstrap — and the same known gap, which is why app *upgrade* is deferred rather than solved. |

## Migrations

| | |
| --- | --- |
| [ADR-0009](0009-one-alembic-history.md) | **One history, driven by the app list.** Importing an app's models *is* registration. The footgun — autogenerate proposing to drop a disabled app's tables — is documented rather than fixed. |
| [ADR-0010](0010-migrations-need-no-flask-app.md) | **The migration environment builds no Flask app.** A broken factory must not also be a broken migration. A test deletes the required secrets to keep that honest. |
| [ADR-0011](0011-revisions-authored-on-the-host.md) | **Revisions are authored on the host, applied in the container.** Code being read-only to the process running it is correct; authoring is not a container's job. |
| [ADR-0032](0032-tables-are-claimed-not-prefixed.md) | **Tables are claimed, not prefixed.** `owns_tables` declares the names an app means to hold, silencing the warning *by recording ownership*. Prefixing was proposed and rejected: the names are flask-security's, it is a migration over live rows, and it cannot reach `roles_users` — built inside a dependency, and owned by nobody until `unclaimed.tables` found it. |
| [ADR-0034](0034-apps-declare-what-they-need.md) | **Apps declare what they need, and podpack checks it.** `needs_tables`/`defines_tables`/`needs_secrets`, with `defined_by` (one app) split from `needed_by` (a set) — 0032's single owner silently replaced the first app to declare a table. A need nothing defines is now a boot failure: a schema-level dependency between apps, looser than an import. Prefix-based ownership was considered and fails on `user`. |

## Configuration

| | |
| --- | --- |
| [ADR-0018](0018-config-in-files-secrets-in-the-environment.md) | **Non-secret host settings in files, secrets in the environment.** Config is reviewable and version-controlled; `ALTER SYSTEM` fails by design. |
| [ADR-0013](0013-environment-split-by-restore-semantics.md) | **Split the environment by what a restore does to it.** `.env` you edit; `secrets.env` comes back verbatim. Mixing them put a manual step in the one procedure that should have none. |
| [ADR-0014](0014-site-names-its-project-and-image.md) | **The site names its compose project and image.** Cheap now; retrofitting means renaming running containers on every deployment. |
| [ADR-0021](0021-engine-options-only-when-set.md) | **Pass engine options only when the site sets them.** Supplying pool defaults made podpack unable to run on SQLite at all. |

## The container substrate

| | |
| --- | --- |
| [ADR-0020](0020-bind-mount-ownership.md) | **A root one-shot fixes bind-mount ownership.** The most expensive lesson in the project: servers drop privilege and cannot write to host directories they do not own, and `PGDATA` must sit one level inside its mount. |
| [ADR-0012](0012-two-stage-image.md) | **Two-stage image.** git, uv and its cache are needed to build and never to run — half the image. Deleting them in a later layer would not have worked. |
| [ADR-0015](0015-postgresql-stays-in-a-container.md) | **PostgreSQL stays in a container.** A pinned `postgres:17` upgrades when the site decides, not when the server does. |
| [ADR-0016](0016-require-the-compose-v2-provider.md) | **Require the Compose v2 provider.** `podman-compose` silently ignores `depends_on` conditions, and every ordering guarantee here rests on them. Measured, not assumed. |
| [ADR-0017](0017-always-rebuild-and-stamp-the-commit.md) | **Always rebuild, and stamp the commit.** Source is baked into the image, so a restart brings back the previous build with nothing saying why. A no-op rebuild costs about six seconds. |
| [ADR-0019](0019-drop-uwsgi-for-gunicorn.md) | **Drop uwsgi; gunicorn calls the factory.** Cancels the planned path-parameterisation work, and breaks the chain that made importing the site package build an application. |
| [ADR-0026](0026-the-substrate-ships-in-the-package-and-upgrades-by-manifest.md) | **The substrate ships in the package and upgrades by manifest.** `podpack substrate init/upgrade`: three-way sync with baselines of what podpack rendered, never-clobber conflicts, append-only configuration — the mitigation ADR-0005's rejection of scaffolding implied. |
| [ADR-0029](0029-postgresql-is-required-mongodb-is-optional.md) | **PostgreSQL is required; only MongoDB is optional.** `require_env("SQLALCHEMY_DATABASE_URI")` is unconditional and the login tables are SQL, so a site cannot decline it — a `--services mongodb` site was produced that could not boot. The *container* stays optional, for the managed-PostgreSQL route. |
| [ADR-0031](0031-the-cli-keeps-its-command-groups.md) | **The CLI keeps its command groups.** Flattening to `podpack init` was built and rejected: the real fault was a help text that hid every command behind a group name, and a flat namespace makes each future command compete for a bare verb. |
| [ADR-0028](0028-core-services-are-overlays-the-site-chooses.md) | **Core services are compose overlays the site chooses.** Profiles cannot carry `depends_on` — a service outside an enabled one is *undefined* and invalidates the project — so each backing store is an overlay named in `COMPOSE_FILE`. Addable, not removable; apps do not declare requirements; SQL stays core while its server does not. |
| [ADR-0027](0027-the-database-port-is-published-only-on-request.md) | **The database port is published only on request.** Nothing inside the suite used it, and it was the one number two sites still had to coordinate — which duly clashed. A `postgres-port` profile forwards one when asked, on a number chooseable per use. (Named `dbport` when this was written; ADR-0028 gave every service its own.) |
| [ADR-0035](0035-apps-declare-what-is-theirs-to-back-up.md) | **Apps declare what is theirs to back up.** Pays the debt ADR-0015 recorded. A *simple* app declares nothing — ADR-0007 fixes where its files live and the mapper registry knows its tables — so `backs_up` exists for what looking cannot establish, chiefly that an empty directory means "stateless" and "broken mount" alike. Services gained dump/restore/verify; a site running MongoDB had no backup of it at all. The one declaration that warns instead of refusing to boot. |
| [ADR-0036](0036-the-host-says-whether-to-believe-the-proxy.md) | **The host says whether to believe the proxy.** `PODPACK_PROXY_HOPS` in `.env`, not a key in `app.toml`: trusting `X-Forwarded-*` is a claim about a deployment, and the same committed config runs on an unproxied laptop and a proxied host. Found by a live reset mail carrying an `http://` link — HSTS made it work and hid it. A site is never told its own address, so a rebound domain needs no redeploy. `base_url` would not have helped and was wrongly recorded as the fix. |
