# ADR-0025: The site wires its own extensions

**Status:** Accepted — its **login** clause is superseded by
[ADR-0033](0033-login-is-core.md). Mail and session policy stand, and the
reasoning below about why none of the three is an *app* is untouched: what
changed is who owns login, not what it is.

**Date:** 2026-08-08

## Context

`holdenweb.com` needs three Flask extensions attached — `flask-mailman` for
mail, `flask-security` for login, `flask-paranoid` for session policy — along
with the config they read. None of them is a feature of the site; between them
they are a good part of what the site *is*.

podpack had nowhere to put them. `create_app` builds the application, installs
the apps and returns, and the only extension point in the whole framework is
`SiteApp.init`, which belongs to an app.

The obvious idea is to make them apps: write thin shim packages so mail and
login appear in the `apps` list like anything else, which would also make their
ordering explicit and let a site drop one by deleting a line. Measuring what
each actually contributes is what ruled it out:

| | blueprints | routes added |
| --- | --- | --- |
| `flask-mailman` | none | 0 |
| `flask-paranoid` | none | 0 |
| `flask-security` | its own, named `security` | 4 — `/login`, `/logout`, `/verify`, `/fs-static/…` |

Two of them are not blueprint-shaped at all, and
[ADR-0002](0002-app-is-a-package-with-one-site-app.md) makes a blueprint the
thing an app is built around. A shim would have to invent an empty one purely
to satisfy the contract, and that fiction would then claim a name, a template
namespace, a data directory, a log directory and a config section — of which
only the config section is wanted. `/_status` would report a data directory for
mail that nothing ever writes to.

`flask-security` fails differently and worse. Its blueprint is created inside
`init_app`, so a shim could either hand `SiteApp` a placeholder — leaving the
app's name divergent from `security.*`, where its routes actually live, which is
exactly the drift [ADR-0003](0003-app-name-is-blueprint-name.md) removed — or
hand it the real one, which does not exist yet at declaration time and which
podpack would then register a second time.

## Decision

`create_app` takes an optional `init`, a `callable(app)` for the extensions and
configuration that belong to the site rather than to any one feature. It runs
after the site's config is loaded and `app.extensions["podpack"]` is populated,
and **before the apps are installed**, so that an app's own `init` can rely on a
service the site registered.

## Consequences

A site now has a factory of its own — which gunicorn is pointed at — that calls
podpack's and passes its wiring in. That is a small amount of ceremony for every
site, and it is the honest amount: a site that configures nothing passes nothing.

The ordering is a promise, so it is tested rather than asserted: an app whose
`init` reads a value the site's `init` set fails with `KeyError` if the two are
swapped.

It widens what can claim `/` under
[ADR-0024](0024-the-front-page-belongs-to-the-site.md). That record says a front
page must arrive as an app because a blueprint registered after `create_app`
returns is too late for the check; a site's `init` runs *before* the check, so a
front page registered there is now honoured too. The decision is unchanged —
`/` still goes to whoever asks first — but the constraint is looser than that
record describes.

Shared wiring is still possible and still not an app: a module exporting
`configure(app)` that several sites' `init` functions call is ordinary code
reuse, and asks nothing of the registry.

## Alternatives considered

**Shim packages presenting the extensions as apps.** Ruled out above: it
requires inventing blueprints for things that have none, and breaks the name
identity for the one that has its own.

**Post-configuration — the site calls `create_app` then wires up afterwards.**
Needs no framework change at all, and works for extensions. Rejected because it
runs after the apps rather than before, so an app could not rely on a site
service; and because it leaves every site to discover for itself that
`app.extensions["podpack"].host_config` is where its settings are.

**Allowing `SiteApp(blueprint=None)` for service-only apps.** Would need a
declared name for those, since the name is derived from the blueprint —
reintroducing the two-names-that-must-agree problem in a narrower form.
