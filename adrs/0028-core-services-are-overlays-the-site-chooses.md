# ADR-0028: Core services are compose overlays the site chooses

**Status:** Accepted — amended by [ADR-0029](0029-postgresql-is-required-mongodb-is-optional.md), which makes PostgreSQL required rather than chosen

**Date:** 2026-08-13

## Context

PostgreSQL was welded into the substrate. `compose.yaml` defined it, `web`
and `migrate` waited on it, `init-storage` chowned its data directory, and
`db-init/` bootstrapped its roles. A site that wanted a document store had
nowhere to put it, and a site that wanted no SQL server at all could not say
so. Both are real: apps are waiting that store documents rather than rows,
and the substrate had no vocabulary for either request.

The obvious mechanism is a compose profile, and it does not work. Measured
before designing anything, because ADR-0016 exists precisely because someone
once assumed a compose behaviour instead of running it:

```
web.depends_on: {gate: {condition: service_completed_successfully}}
gate: {profiles: ["db"]}          # profile not enabled
->  service "web" depends on undefined service "gate": invalid compose project
```

A service outside an enabled profile is not merely absent — it is
*undefined*, and the whole project is rejected. So a profile can only make a
service optional by making everything that waits for it optional too, which
is the ordering guarantee ADR-0016 was written to protect.

`COMPOSE_FILE` overlays do work, and were verified at runtime rather than in
`config` output: a base file plus `compose.postgres.yaml` that adds both the
server *and* the `depends_on` edge produced a stack where the gate ran to
completion before `web`, while the base alone ran `web` by itself.

## Decision

Every backing service is an overlay. `compose.yaml` is the base — the site,
its storage, and the migration gate — and `COMPOSE_FILE` in `.env` names the
overlays a site runs. A service's own file carries its server, its storage
one-shot, its on-request port forwarder (ADR-0027, now one per service) and
the `depends_on` edges that make the rest of the stack wait for it.

A service declares **one thing, its name**, and the rest derives from it —
ADR-0003's move applied to the substrate: `compose.<n>.yaml`, `init-<n>`,
`<n>-port`, `<N>_HOST_PORT`, `<N>_BIND_ADDR`, `PODPACK_SERVICE_<N>`,
`<n>-init/`, `<N>_URI`. `postgres` satisfies nine of the eleven derived
names already, which is why the rule reads as discovered rather than
imposed; its two exceptions are declared in the catalogue with their
reasons.

**Which services a site runs is the site owner's decision, taken
independently of its installed apps.** An app cannot declare that it needs
one. Services are **addable** — `podpack substrate services --add mongodb`
— and deliberately not removable: taking a store away is a decision about
data, not configuration, and the engine has no verb that deletes a file.

**SQL is the one store an app may assume.** `db`, its single metadata and
its one alembic history stay core (ADR-0009, ADR-0010), so the alembic
environment lives in the base rather than in postgres's overlay. What is
optional is the *server*: a site may point `SQLALCHEMY_DATABASE_URI` at a
managed PostgreSQL and run no `postgres` container, which ADR-0015 already
required to be a change to one variable. A site running no SQL at all keeps
every part of podpack except `db` — the registry, templates, nav, per-app
data and logs, config and `/_status` — and its `migrate` service has nothing
to do.

## Consequences

Two sites need no coordination beyond their web port: the database ports
were the last thing they shared, and ADR-0027 had already made those
on-request. A site that wants both stores says so in one line; a site that
wants neither is a valid site.

Costs, honestly. **The declaration is written twice** — `COMPOSE_FILE` in
`.env` because that is the only language compose speaks, and the recorded
service list in `substrate.json` because podpack must render the right
configuration canon. That is the same tax `SITE_NAME` pays for the same
reason, and nothing checks the two agree until something reports it.
**Every service's files are installed whether or not the site runs it**, so
a postgres-only site carries an inert `compose.mongodb.yaml`; the
alternative needed per-site installation state that could drift, and this
way `status` stays total and the byte-identical dogfood test covers every
service for free. And **a service is added in four movements** — the
command, the secrets by hand, the host directories, the rebuild — because
podpack writes neither credentials nor host state.

Two container behaviours are now load-bearing and were both measured:
compose **replaces** `command:` while concatenating `volumes:`, so every
service brings its own storage one-shot rather than adding a mount to a
shared one — a shared `init-storage` would silently lose its chown the
moment an overlay touched it. And a top-level extension field is
interpolated before the project is built, so
`x-podpack-services: ${COMPOSE_FILE:?...}` refuses a stack whose declaration
went missing with a sentence, rather than seconds later with `web` unable to
resolve a hostname.

## Alternatives considered

- **Compose profiles.** The measurement above. They remain right for the
  port forwarders, which nothing depends on.
- **Rendering `compose.yaml` per site**, with the enabled services
  substituted in. It would work and it would make the substrate's most
  heavily commented file a template with conditional blocks, which the
  token-replacement renderer cannot express and a real template language
  would have to earn.
- **A `compose.override.yaml` a site owns.** Equivalent in effect and
  un-upgradable by construction — precisely the drift ADR-0026 exists to
  end.
- **Apps declaring their needs** (`SiteApp.requires`). Designed, then cut on
  the site owner's decision: it raises a dependency-management question —
  what an unmet requirement does, whether it is fatal or degrading, what
  happens when two apps disagree — that nobody has to answer if the site
  simply says what it runs.
- **MongoDB as an installed app.** It is not a feature with endpoints, and
  ADR-0025's reasoning applies: a `SiteApp` is built around a blueprint, and
  a database has none to invent.
