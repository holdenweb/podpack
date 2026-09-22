# ADR-0039: The hop count follows the deployment

**Status:** Accepted

**Date:** 2026-09-22

**Amends:** [ADR-0036](0036-the-host-says-whether-to-believe-the-proxy.md),
which stands in every respect but one: *how* the host says it.

## Context

ADR-0036 settled that a podpack site reads `X-Forwarded-*` only when its host
declares how many proxies stand in front of it, and that the declaration
belongs in `.env` rather than `config/app.toml`. That reasoning is unchanged.
What the year since has shown is that the declaration does not reliably happen.

`PODPACK_PROXY_HOPS` is the one variable podpack cannot deliver. Its entire
documentation lives in `env.example`, which is a *seeded* substrate file:
`podpack substrate upgrade` never rewrites a seed (ADR-0026), so an existing
site is never told the variable exists. holdenweb.com's own `.env.example`
mentions it zero times and always will, while podpack's ships it. The site was
upgraded to the release that introduced it, reported every file `ok`, and
remained silently short of the one thing that release was for.

The cost of that silence is the whole reason ADR-0036 exists. A site missing
the line serves every page correctly and gets only absolute URLs wrong, so the
first symptom is a password-reset mail carrying an `http://` link with a token
in the path.

The manual handling spread accordingly. `holdenweb.com/ops/rebuild.md` gives a
whole part to it, headed "the setting nothing will tell you about";
`rebuild.sh` reconciles it by hand; `restore.sh` carries it in the list of
keys that describe the host rather than the site. Three procedures exist to
remember one number.

**And the number was never a free choice.** It is a function of how the site
is deployed, and podpack already records that: `PODPACK_ENVIRONMENT`, which
ADR-0037 gave to `configure-host.py` to own, and which *is* delivered.

## Decision

Derive the count from the deployment, and keep the declaration as an override.

| `PODPACK_ENVIRONMENT` | `PODPACK_PROXY_HOPS` | Hops trusted |
| --- | --- | --- |
| unset or `local` | unset | 0 |
| anything else | unset | 1 |
| any | set | whatever it says |

One, not a guess at the general case: it is the only topology this substrate
deploys into, and the substrate is what puts a site behind a proxy at all.

**An unrecognised value counts as not-local**, matching
`prepare-host-dirs.sh`, which made the same choice for the same reason. A typo
must not quietly turn a real deployment into a lab. Here the consequence of
getting that direction wrong would be a proxied site declining to read
`X-Forwarded-Proto` because somebody capitalised the word, which is the
original bug reintroduced by a spelling mistake.

**An explicit `0` is not the same as saying nothing**, and the implementation
keeps them distinct: `declared_hops()` returns `None` for absent rather than
falling back, so a non-local deployment can decline the hop it would otherwise
be given.

`/_status` gains `hops_from` and `environment` alongside `hops_trusted`,
because a derived count and a declared one are corrected in different files
and the number alone does not say which you are looking at.

## Consequences

**The common case needs no line, which is the point.** A managed host gets the
right behaviour from the variable `configure-host.py` already writes. Nothing
has to be remembered, delivered, or carried through a restore.

**Unset no longer means zero on a non-local deployment, and that is a
behaviour change with a security direction.** A site with
`PODPACK_ENVIRONMENT=staging` and no `PODPACK_PROXY_HOPS` will begin trusting
one hop when it upgrades to a release carrying this. That is right for every
deployment this substrate builds, because they bind loopback and sit behind
one nginx. It is wrong for a non-local deployment reachable directly, which
must now set `PODPACK_PROXY_HOPS=0`. ADR-0036's closing warning therefore
gains force rather than losing it: **a deployment that widens
`WEB_BIND_ADDR` must revisit this**, and now has one more reason to.

**A site upgrading gets the fix without being told**, which is the entire
argument. It is also the reason this could not be done by improving
`env.example`: a seed reaches new sites only, and the sites that need this are
the ones that already exist.

**The manual handling can retire, but not yet and not here.** The three
procedures in holdenweb.com that set this by hand stay correct until that site
runs a podpack release carrying this decision. An explicit value keeps
winning, so they do no harm in the meantime; retiring them is a follow-on
after the upgrade, not part of this change.

**Backlog item 33 gets easier.** It wants the `web` healthcheck interval to
vary by environment. `deployment_environment()` is now the one place that
question is answered.
