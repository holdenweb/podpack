# ADR-0014: The site names its compose project and image

**Status:** Accepted

**Date:** 2026-08-08

## Context

Until `30f52e6`, `compose.yaml` opened with `name: holdenweb-lab-pg` and both
build services tagged `localhost/holdenweb-lab-pg-web:latest`. Every deployment
made from this tree was therefore called the same thing. Since running two sites
means two deployments rather than one process serving two domains
([ADR-0001](0001-one-site-per-instance.md)), a second site on one host is a
question of when, not whether — and two of them would have produced two sets of
identically-named containers and one image tag that each rebuild took from the
other.

The names could have been changed the day that happened. That is the expensive
day: renaming containers that are running and re-tagging images on every existing
deployment, rather than editing one line while there are none.

## Decision

`SITE_NAME`, in `.env`, names the compose project (`compose.yaml:1`) and the
image the `migrate` and `web` services build and run (`:125`, `:148`), defaulting
to `podpack` if unset. It sits in `.env` because a deployment's identity is
exactly the kind of thing expected to differ on a new host, which is what that
file is for; `secrets.env` is never read for substitution and could not supply it
anyway.

## Consequences

`podman ps` now answers which site a container belongs to —
`holdenweb-lab-postgres-1`, `holdenweb-lab-web-1` — instead of showing two
indistinguishable sets.

It isolates names and not ports. `WEB_HOST_PORT` and `POSTGRES_HOST_PORT` still
have to be chosen per deployment, and a clash fails at container start:
publishing an already-bound 8458 gives `bind: address already in use`, checked
against the running stack rather than assumed. Nothing warns beforehand.

The site's name is now written twice, in `.env` and as `[site] name` in
`config/app.toml`, and nothing checks that the two agree. Compose cannot read
TOML, which is the whole reason.

Changing `SITE_NAME` on a live deployment orphans it. Compose finds containers by
project name, so `SITE_NAME=other-site podman compose ps` lists nothing while the
old containers keep running and the old image tag stays behind. A rename means
bringing the stack down under its old name first.

## Alternatives considered

Deriving the project name from `config/app.toml`. Compose cannot read TOML, and
that field is a display string — `"holdenweb.com (podman PostgreSQL lab)"` —
carrying spaces and brackets a container name cannot.

Leaving `compose.yaml` hardcoded and passing a project name on the command line.
That puts the site's identity in a flag every invocation has to remember, where
`.env` is read whether anyone remembers or not.
