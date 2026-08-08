# ADR-0023: Do not warn when a blueprint name differs from its module name

**Status:** Accepted

**Date:** 2026-08-08

## Context

A site lists *import* names in `apps` and keys everything else on the app's own
name, which is its blueprint's ([ADR-0003](0003-app-name-is-blueprint-name.md)).
The two are routinely different. `src/podpack_notes/views.py` builds
`Blueprint("notes", …)`, so `podpack_notes` is imported and `notes` is what keys
`[site.mounts]`, `[apps.notes]`, `src/podpack_notes/templates/notes/` and the
host data and log directories.

That divergence is deliberate. A distribution wants a namespaced name, because
it competes for one on an index and in a dependency list; an app wants a short
one, because the app's name is what a reader meets in URLs, template paths and
directory names. Forcing them equal buys consistency and pays for it with
`templates/podpack_notes/` and `[apps.podpack_notes]` in every config file — the
good case made worse to guard against a bad one.

The bad case is already covered twice. The mistake worth catching is
`Blueprint(__name__)`, which yields a dotted name, and Flask 3.1.3 refuses it
outright: `ValueError: 'name' may not contain a dot '.' character.` A name
generic enough to collide with another app's is caught in `registry._install`,
which refuses to boot when two installed apps share a blueprint name. What
remained was not a validation gap but a discoverability one: the registry knew
which import name each app came from and discarded it, leaving the `_check_mounts`
boot failure as the only way to learn the mapping.

## Decision

podpack neither warns nor errors when an app's blueprint name differs from its
module or distribution name. Commit `684d6a3` reports the mapping instead:
`PodpackState.installed_from` in `src/podpack/registry.py` records each app's
name against the import name it was installed from, and `/_status` in
`src/podpack/core.py` shows it per app. A warning that fires on a correct,
deliberate arrangement is noise, and noise teaches people to ignore warnings.

## Consequences

A reader still has to know which of the two names keys `[site.mounts]` and
`[apps.<name>]`, and gets it wrong until they look. Reporting is a weaker
instrument than validating: `/_status` answers only for someone who thinks to
ask, and only on a site already running. So the `_check_mounts` message still
spells the distinction out ([ADR-0006](0006-mount-points-belong-to-the-site.md)),
and `test_the_import_name_is_recorded_for_reporting` pins the mapping.

The uniqueness check fires only on an actual collision. One app naming its
blueprint `main` installs cleanly and takes `main` for its templates, its config
section and its directories, with nothing said.

## Alternatives considered

**Warn on divergence.** It would fire on `podpack_notes` — the reference app,
every boot. A warning the reference implementation trips is one everybody learns
to filter, and it would then be filtered when something real needed saying.

**Require the two to be equal.** This is the same trade as forcing them equal by
convention, made enforceable: it forbids the namespaced-distribution and
short-app-name pairing outright and drags the distribution name into template
paths and config sections.

**Derive the app's name from its module rather than its blueprint**, which makes
divergence impossible by construction. Rejected in ADR-0003 for its own reasons —
the blueprint's name is already the app's public identity, prefixing every
endpoint — and it would cost the short name too.
