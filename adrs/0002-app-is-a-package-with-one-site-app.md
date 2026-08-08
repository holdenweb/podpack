# ADR-0002: An app is a package exposing one `site_app`

**Status:** Accepted

**Date:** 2026-08-08

## Context

A site is supposed to gain a feature by an edit to a config file and a restart,
so podpack has to turn an import name into an installed feature knowing nothing
about it in advance. Flask supplies most of that already: register a blueprint
and it brings its own views, templates, static files and CLI commands. Being
precise about the remainder mattered, because a framework supplying more than
the remainder ends up competing with the one underneath it.

The remainder is four things, all of which a Django app bundles and a Flask
blueprint does not: models that migrations can see, a declaration of what is
installed, somewhere to put per-app data, and a way to contribute to the site's
navigation. The first is the sharp one. Autogenerate reads `db.metadata` after
building an app, so a model imported lazily — on first request, say — is
invisible to it, and the app's tables are silently never created.

## Decision

An installable app is a Python package with a single module-level
`site_app = SiteApp(...)`; `src/podpack/registry.py`, commit 27eb69d. Installing
one is `import_module(name)` followed by `getattr(module, "site_app")`, and a
module without one raises at boot naming the contract. `SiteApp` carries only
what Flask does not — the blueprint, the prefix it asks to be mounted at, its
nav entries, and an optional `init(app)` run before registration. Everything
else is convention that `_install` finds by looking: `models.py` imported if it
exists, `templates/<name>/` namespaced under the app's name, `data/` copied to
the host directory the first time that directory is empty.

## Consequences

Convention is silent when it is wrong. `_import_if_present` uses `find_spec`
rather than catching `ImportError`, so a genuine failure inside `models.py`
still raises instead of reading as "this app has no models" — but a file misnamed
`model.py` is indistinguishable from an app with no schema. Nothing is said, no
tables appear, and the first sign is a query failing.

One `site_app` means one blueprint. An app wanting a second URL tree registers
it from `init`, which is handed the Flask app, or ships as two packages.

Installing is importing, so an app's import must do nothing but define things.
The same import runs in the migration environment through `import_app_models`,
where there is no Flask app to depend on.

`data/` seeds once and there is no once-only code hook, so upgrading an app
whose shipped data has changed is unsolved — deliberately, since no app yet
ships data that changes with its code.

## Alternatives considered

**A bare blueprint**, which is what `pp-pdf` exposed through its
`holdenweb.apps` entry point, and still does. A blueprint can be found and
mounted, but models, data and nav have nowhere to hang, so the site restates
them by hand — the hardcoded `SECTIONS` list at
`holdenweb.com/src/holdenweb/__init__.py:60` is
what that looks like after a few years.

**Entry points as the mechanism rather than an extra.** They discover what is
installed; they do not decide what is *enabled*, nor in what order, and both nav
and `init` depend on order. Worth revisiting as a hybrid once the app list feels
like a chore.

**Scanning for distributions named `podpack-*`.** Presence in the image would
become enablement, so switching a feature off would need a rebuild; and an
editable install, which is how an app is worked on locally, need not register
the metadata such a scan reads.
