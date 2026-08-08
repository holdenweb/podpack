# ADR-0024: The front page belongs to the site

**Status:** Accepted

**Date:** 2026-08-08

## Context

podpack registered a default front page at `/` on its core blueprint,
unconditionally, so that a site with no apps installed showed something rather
than a 404. That is a reasonable thing to want. Registering it as a fixture was
not.

Preparing to adapt `holdenweb.com` exposed the cost. That site's `/` *is* the
site — the tagline, the sections, the whole front page. Installing it alongside
podpack produced two rules for one address:

```
rules for '/': ['podpack.home', 'main.<lambda>']
GET / returns: <p>site chrome</p>… No apps are installed yet
```

Both registrations succeeded. Werkzeug matched whichever rule was added first,
which was always podpack's, and nothing anywhere reported a conflict. The site
simply did not have a front page, and the reason was invisible.

This contradicted [ADR-0006](0006-mount-points-belong-to-the-site.md) in the one
place it matters most. That record establishes that an app only *asks* for a
mount point and the shape of the address space stays the site's — while the
framework was quietly holding the most valuable address in it.

## Decision

The default front page is a fallback, not a fixture. `install_home_page` runs
after the apps are installed and registers `/` only if no rule already claims it.
`/healthz` and `/_status` remain unconditional, under names a site is unlikely to
want.

## Consequences

An app mounted at the site root now serves the site's front page, and podpack
yields without being asked to. A site with no apps still gets the default, so
nothing regresses for a site that has not been built yet.

A site's front page has to arrive as an **app**, not as a blueprint registered
after `create_app` returns: the check has already run by then, and a late `/`
loses exactly as podpack's used to win. That is a real constraint, and it is the
same shape as the rest of the contract — an app is already where a site's
templates, nav entry and config namespace live, so its front page is not an
exception.

The check is by URL rule rather than by asking apps what they claim, so it costs
nothing to declare and cannot drift from what was actually registered. It also
means the *first* app to route `/` takes it and a second is silently ignored, in
installation order — the same failure the framework just stopped committing,
now possible between two apps. Nothing detects that today.

## Alternatives considered

**Move the diagnostics off `/` and drop the default page entirely.** Simplest,
and it makes the site's ownership absolute. Rejected because a freshly created
site would 404 on its own root, which reads as broken rather than as empty.

**Register the core blueprint after the apps.** Would fix the ordering for `/`
but leaves `/healthz` and `/_status` losing to an app that happened to claim
them — trading a likely collision for an unlikely but more damaging one.

**Let the site pass its own home view to `create_app`.** A second way to do what
an app already does, and it would leave the front page as the one part of a site
that is not an app.
