# ADR-0005: Template resolution runs site, then app, then framework

**Status:** Accepted

**Date:** 2026-08-08

## Context

Jinja finds a template by name, and Flask assembles one dispatching loader over
the application's template folder and every blueprint's. Two consequences forced
a choice when podpack acquired installed apps in `27eb69d`. Two apps both
shipping `index.html` collide, and the winner is whichever blueprint registered
first — a silent wrong answer that changes with the order of the `apps` list.
And an app that extends `base.html` cannot know whether the site it has been
installed on has written any chrome; on a site that has not, the render dies.

An app cannot be asked to answer either question. It ships before the site that
installs it exists, and it is mounted wherever the site says.

## Decision

Templates resolve site, then app, then podpack's own defaults. Apps namespace
theirs under `templates/<app name>/` — the name derived from the blueprint, so
the namespace cannot drift from the endpoints. `create_app()` passes
`site_package` as Flask's import name, which makes the *site* the application,
so Flask's existing ordering gives site override for nothing. The only wiring
podpack adds is `_add_template_fallback()` in `src/podpack/__init__.py`, which
wraps the finished dispatching loader in a `ChoiceLoader` with
`PackageLoader("podpack", "templates")` after it. Last, so it can never
intercept a template a site or an app has answered.

## Consequences

An app renders against sensible chrome on a site with none, and never learns
which layer answered. Comment out the single call in `create_app()` and exactly
one of the 23 tests fails — `test_app_renders_on_a_site_with_no_chrome`, with
`jinja2.exceptions.TemplateNotFound: base.html`.

The costs. Namespacing is convention, not enforcement: the registry rejects two
apps sharing a blueprint name, but nothing checks that an app's files live under
it, and nothing checks the blueprint was given `template_folder="templates"` —
forget that and its templates are simply invisible. Overriding is shadowing by
path, so a site that overrides couples itself to an app's internal template
layout; the app renames a partial in a later version and the override stops
being consulted, with no error and a page that quietly reverts. podpack's
`base.html` is now an interface — its `content` block is what apps extend — so
the default chrome cannot be restructured freely. And resolution happens at
render time, so a misspelt path falls through all three layers and 500s on one
request, unlike a bad nav endpoint, which refuses to boot.

## Alternatives considered

Prepending podpack's loader instead of appending: inverts the point, since the
framework's `base.html` would then beat the site's.

Scaffolding a copy of podpack's templates into each new site: the floor becomes
a snapshot taken at creation, and every site drifts from it separately.

Requiring each app to ship complete standalone chrome, as `pp-pdf` does outside
podpack: every app then repeats the layout and the site cannot restyle it.
