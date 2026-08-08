# ADR-0003: An app's name is its blueprint's name

**Status:** Accepted

**Date:** 2026-08-08

## Context

`SiteApp` carried a declared `name` field beside the `Blueprint` it wrapped. The
two had to agree, and nothing checked that they did.

They went wrong quietly because they are read on different paths. The registry
in `src/podpack/registry.py` uses `site_app.name` at install time: it is what
`prepare()` makes the data and log directories from, what namespaces the
templates, and what keys `[apps.<name>]`. View code resolves the app at request
time from `request.blueprint` — both `data_dir()` and `app_config()` default
their argument through `paths._current_app_name()`. So when the two diverged, the
registry created and logged into one directory while the views read and wrote
another, and `app_config()` returned `{}` — it ends in `apps.get(name, {})` —
leaving every setting on its default. Nothing was raised, at boot or in the
request.

`pp-pdf`, the first app written outside this repo, hit it. It carried a warning
comment above `name="pp_pdf"` saying that nothing checked the two agreed, plus a
test, `test_app_name_matches_blueprint_name`, asserting they did. A consumer
package was policing an invariant belonging to the framework it consumes.

## Decision

`SiteApp.name` is a property returning `self.blueprint.name`. It is not a field,
and `SiteApp(name=...)` raises `TypeError`. The blueprint's name is the right
source because it is already the app's public identity: it prefixes every
endpoint, and so appears in every `url_for` and every nav entry, and it is what
podpack resolves an app from at runtime. Naming the blueprint is the decision.

## Consequences

Every read of `site_app.name` is unchanged; only construction is. An app drops a
line, and passing `name=` becomes a `TypeError` rather than a hazard — which is
a breaking change to the contract, paid once while there were two apps to fix.

What it removes from the apps themselves is the more telling part: `pp-pdf` had
been carrying a warning comment and a test asserting the two names matched, both
policing an invariant that was never its to police. There is nothing left there
to assert.
`tests/test_registry.py` picks up both halves: that the derived name is what
`data_dir()` and `app_config()` resolve to in a request, and that passing `name=`
is an error.

What it costs. The blueprint's name now carries weight that is not visible where
it is written: renaming a blueprint renames the host data directory and the config
section, so an app that renames one orphans its own data. Nothing migrates that —
the old directory just becomes an entry `unclaimed()` reports. An app has also
lost the freedom to be called one thing in URLs and another on disk. That is the
point, but it is a real degree of freedom removed.

What this does not fix is import name against app name: `podpack_notes` is listed
in `apps` and answers to `notes`. `PodpackState.installed_from` and the
`[site.mounts]` error message carry that distinction, and still must.

## Alternatives considered

**Validate rather than derive** — raise at install time when the two disagree.
The smallest change, and exactly what `pp-pdf` was doing from outside. It leaves
every author writing the same name twice for the privilege of being told off when
they get it wrong.

**Declare the name and build the blueprint from it.** That takes `Blueprint`
construction away from the app, and with it `template_folder`, `static_folder`
and everything else Flask puts on that constructor — too much surface to swallow
for one string.

**Keep both and document the invariant.** A documented rule nothing enforces is
what produced the `pp-pdf` comment in the first place.
