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
| [ADR-0025](0025-the-site-wires-its-own-extensions.md) | **The site wires its own extensions.** `create_app(init=…)` for mail, login and session policy — which are not apps, because two register no blueprint and the third brings its own. |

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
