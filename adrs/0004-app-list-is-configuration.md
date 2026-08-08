# ADR-0004: The installed-app list is configuration, not code

**Status:** Accepted

**Date:** 2026-08-08

## Context

Flask hands a blueprint its views, templates, static files and CLI commands for
nothing, but nothing that says what a *site* consists of. The site that
prompted this framework answers that in source:
`holdenweb.com/src/holdenweb/__init__.py` is 319 lines of factory, models and
views, with blueprints registered by hand and a `SECTIONS` list hardcoded at
line 60.

Under the container substrate that is worse than untidy. Framework source is
baked into the image, so anything expressed in code is a rebuild, and the thing
most likely to differ between the laptop, staging and a real host would have
been the one thing that could not be varied without one.

## Decision

A site names its apps as import names in `[site] apps` in the host-mounted TOML
(`config/app.toml`, bind-mounted read-only at `/etc/holdenweb/app.toml`).
`installed_apps()` in `src/podpack/config.py` reads it, `create_app` passes it
to `install_apps`, and the registry does `import_module` on each name in the
order given. Adding or removing a feature is an edit to that file and a
restart. It sits in the config file rather than the environment because it is
emphatically not a secret, and reviewing a deployment should mean reading a
file.

## Consequences

Verified against the running lab rather than by reading: emptying `apps` and
running `podman compose restart web` — no rebuild, same image — took `/notes/`
from 200 to 404, took the app's nav entry off the front page, and left
`/_status` reporting `apps: {}` with `notes` under `unclaimed`. Restoring the
line brought all of it back.

Order is now load-bearing and silent about it: nav appears in installation
order, and an app's `init` may depend on a service an earlier one registered.

`podpack.migrations.target_metadata()` reads the same list, so autogenerate
sees exactly the apps enabled. Generating a revision with one switched off
really does propose `op.drop_table` for its tables.

Two names are now in play: the list holds *import* names (`podpack_notes`)
while mounts, config sections and data directories key on the blueprint name
(`notes`). `/_status` reports `installed_from` so the mapping is answerable by
looking.

The list is also not the only list. A distribution absent from the image needs
`uv add` and a rebuild before the line does anything, and nothing reconciles
the two in advance — a name that will not import is a boot failure, and that is
the whole site rather than one feature.

## Alternatives considered

**Discovery by scanning for a `podpack-` distribution name prefix.** Rejected,
and recorded in 58d7884 because it is a natural idea to have twice. A scan
finds what is installed but installs nothing, so the dependency entry is needed
either way; what it would actually replace is this list, the part deciding what
is *enabled* without a rebuild and in what order. Discovery-by-presence makes
in-the-image mean switched-on. And it finds nothing under an editable install —
how an app is worked on locally — so it would work in the built image and fail
quietly on the bench. The prefix stays a convention nothing reads.

**Entry points.** Deferred rather than rejected: they impose no naming and
`pp-pdf` already ships one. The trigger is the app list becoming a chore, and
the shape is a hybrid — entry points for discovery, this list for ordering and
enablement.
