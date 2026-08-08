# ADR-0022: Navigation is contributed by apps and addressed by endpoint

**Status:** Accepted

**Date:** 2026-08-08

## Context

`holdenweb.com` keeps its navigation in a module-level constant at
`src/holdenweb/__init__.py:60` — `SECTIONS = [Section("Home", "/"), ...]`, three
entries holding literal paths, in the same file as the factory, the models and
the views. Adding a feature therefore meant editing the core package, which is
exactly what installing an app is supposed to avoid: under
[ADR-0004](0004-app-list-is-configuration.md) a feature arrives as a line in
`apps` and a restart, and a nav constant would have put half of that back as a
code change.

Literal paths had a second problem by the time podpack existed. A site overrules
where an app is mounted ([ADR-0006](0006-mount-points-belong-to-the-site.md)), so
a path baked into a nav entry is a claim the site is free to falsify — and it
fails by pointing somewhere wrong, not by refusing to boot.

## Decision

Apps declare their nav on their `SiteApp` as a tuple of `Section`
(`src/podpack/nav.py`), the registry appends each app's entries to
`state.nav` as it installs it, and a context processor puts the assembled list in
front of every template. A `Section` holds a label and a **Flask endpoint name**,
never a URL; the chrome resolves it with `url_for` as it renders. An entry naming
an endpoint no view provides is a boot failure.

## Consequences

An app follows its mount with nothing restated on either side: the test at
`tests/test_registry.py:49` moves `notes` to `/writing/notes` and asserts the
rendered nav link moves with it.

Strictness is not fastidiousness. `base.html:33` calls `url_for(section.endpoint)`
in the header, and every page extends it, so one bad entry raises `BuildError` on
*every* page rather than 404ing on the one it points at. `registry._check_nav()`
runs immediately after `register_blueprint`, the first moment the app's routes
exist, and names the app, the label and the endpoint.

Two costs. Nav order is installation order, so it is decided by the `apps` list —
one list now carries both nav sequence and `init` ordering, and a site that wants
a different order has to accept whatever that implies for the other. And podpack
gives a site no way to reorder or relabel an entry at all: a site that wants
"Writing" where an app says "Notes" has no lever short of shadowing a template.

`82803db` showed how quietly this can rot. `base.html` was converted to endpoints
and `index.html` was not; Jinja renders the now-undefined `section.path` as `""`
and says nothing, so the home page carried blank links with the suite green. The
nav test asserts the link appears twice and that no `href=""` survives.

## Alternatives considered

A nav table in the site's config file, which is the honest answer to the
reordering cost. It loses on the same ground `_check_mounts()` exists to police:
a second table naming apps is a table that drifts from the app list.

Keeping paths in `Section` and adjusting them when a site remounts an app. It
makes the mount override a two-sided edit and turns a boot failure into a broken
link nobody notices until someone clicks it.
