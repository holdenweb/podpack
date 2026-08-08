# ADR-0006: Mount points belong to the site, not the app

**Status:** Accepted

**Date:** 2026-08-08

## Context

An app's `url_prefix` used to be a claim on the site's address space. The
registry passed it straight to `register_blueprint`, so the site's only lever
was whether the app appeared in `apps` at all. Adding a feature meant accepting
whatever URL its author happened to pick — the wrong way round, because the app
list decides *whether* something is installed while the shape of the address
space belongs to the site.

The first fix (82803db) let a site overrule the app, but put the override in
`[apps.<name>]`, which then held two kinds of setting with two different owners.
That mixing was not theoretical: `app_config()` handed the app its own
`url_prefix`, presenting a decision the app takes no part in as though it were
one of its settings.

## Decision

`SiteApp.url_prefix` is a request. A site overrules it in `[site.mounts]`, keyed
by the app's name, next to the app list and the rest of the site's policy; only
apps being moved need an entry, so the table doubles as the site's map of
everywhere it has chosen to put something, and `[apps.<name>]` goes back to
meaning one thing. The registry `replace`s the frozen `SiteApp` rather than
using a local variable, so `state.apps` — and therefore `/_status` — describes
where the app actually ended up rather than where it asked to go.

## Consequences

A separate table can drift from the app list, which the old spelling could not.
`_check_mounts` refuses to boot on a mount no installed app answers to, run
after installation because that is the first moment the app names are known.
Ignoring the stray entry would leave the app at the address it asked for, which
is precisely the address the site said it did not want.

The key is the app's name — its blueprint's name — and not always the import
name in `apps`: `podpack_notes` is imported and answers to `notes`. That trap is
paid for twice, in the error message and in `installed_from`, which exists so
`/_status` can report the mapping.

Relocation costs nothing elsewhere only because `Section` holds an endpoint
rather than a path, so nav resolves through `url_for` as it renders.

## Alternatives considered

**Leave mounts in `[apps.<name>]`.** One table, no orphan check — but
`app_config()` leaks the site's decision back to the app, and no guard can
untangle two owners sharing a namespace.

**Ignore the old `url_prefix` spelling.** A silent downgrade: a site that had
not been updated would come up at the address the app asked for with nothing
said. `_reject_mount_in_app_config` fails instead, spelling out the replacement,
which costs one edit per existing site.
