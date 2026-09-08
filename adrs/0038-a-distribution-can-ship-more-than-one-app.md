# ADR-0038: A distribution can ship more than one app

**Status:** Accepted

**Date:** 2026-09-07

## Context

Every app that existed when the plugin contract was written was one app in one
distribution: `podpack-notes` ships `podpack_notes`, `podpack-qrcode` ships
`podpack_qrcode`, `pp-pdf` ships `pp_pdf`. The naming convention settled on
2026-08-08 — distributions are named `podpack-<app name>` — reads naturally as
*one app per distribution*, and nothing had tested the other reading.

`podpack-pages` broke the symmetry. It renders HTML/Markdown content trees, and
a site turned out to want three of them at once, mounted separately and each
with its own navigation: the plain `pages` content at `/pages` with no nav entry,
a Python course at `/pybooks` under a "Python" entry, and a blog at `/blog` under
a "Blog" entry. The three share one body of content-serving code — the site
chrome, the templates, the asset-rewriting and tree-reading machinery. `pybooks`
reuses the pages engine outright (it is `PagesApp` under another name); `blog` is
a distinct kind built on the same toolbox, with its own views, manifest reader
and publish contract. Splitting them into three distributions would have
triplicated packaging — three `pyproject.toml`s and three release cycles — for
one codebase that changes together.

The question was whether podpack's model even permits one distribution to offer
several apps, or whether the one-app-per-distribution reading was load-bearing.
It is not. An app's identity is its blueprint name (ADR-0003), and a site enables
an app by listing its *import name* in `apps` (ADR-0004) — neither is tied to the
distribution that packages it. `podpack-pages` 0.3.0rc1 exports three
module-level `site_app`s at three import names — `podpack_pages` (the `pages`
app), `podpack_pages.pybooks`, and `podpack_pages.blog` — and holdenweb.com
enables all three by listing those three names. Nothing in the resolver, the
registry or the migration environment needed changing; the existing design
already allowed it. What was missing was a record saying so, before someone
"corrected" a multi-app distribution back toward the convention.

## Decision

**A distribution may ship more than one app.** The distribution is the unit of
*packaging and dependency* — one `uv add`, one entry in `[tool.uv.sources]`, one
version — and says nothing about how many apps it contains. An app remains what
ADR-0002 and ADR-0003 define: a package (or subpackage) exposing one module-level
`site_app`, identified by its blueprint's name. A site enables each app it wants
by listing that app's import name in `apps` (ADR-0004) and mounts each where it
likes (ADR-0006); the apps from one distribution are enabled, ordered and mounted
independently of one another, exactly as apps from separate distributions are.

Apps in one distribution may share code freely — `podpack_pages.pybooks` is the
pages app's own `PagesApp` under a second name, while `podpack_pages.blog` is a
separate `SiteApp` kind that shares only the chrome and the tree-reading toolbox
— because the registry keys everything off the blueprint name, not the
distribution.

## Consequences

The `podpack-<app name>` naming convention (2026-08-08) is now explicitly a
convention for the common single-app case, not a rule: a distribution's name no
longer predicts the apps inside it, and `podpack-pages` ships `pages`, `pybooks`
and `blog`. Anything that assumes distribution name equals app name is wrong —
which is why the entry-point-by-prefix discovery ADR-0004 already rejected would
have been wrong here too, finding one "app" where there are three.

Dependency and enablement now sit at different granularities, deliberately:
adding the distribution (a rebuild) makes all its apps *available*; a line in
`apps` per app makes each one *enabled* (a restart). That is the same two-step
ADR-0004 describes, now visibly independent per app rather than per distribution.

The cost is that a distribution shipping several apps that share code can couple
their versions: a change for one app releases all of them, since they are one
package. That is the price paid to avoid triplicating a single codebase, and it
is the right trade only while the apps genuinely are one codebase — three apps
that diverge would be better as three distributions.

## Alternatives considered

- **One app per distribution, enforced by the convention.** Would have forced
  `pages`, `pybooks` and `blog` into three repositories and three distributions
  for what is one shared content-serving codebase — triplicated packaging and
  version churn, and three places for the shared code to drift.
  Rejected: the convention was always documented as convention only, and the
  registry never depended on it.
- **One `pages` app that switches behaviour by configuration and mount.** A
  single app, installed once, told by `[site.mounts]` and `[apps.pages]` to serve
  three trees. Rejected: the three want *distinct* nav entries, mounts, data
  directories and independent enable/disable, all of which the app list gives for
  free when each is its own import name — and one app cannot contribute three
  independent nav sections or be mounted at three prefixes.
