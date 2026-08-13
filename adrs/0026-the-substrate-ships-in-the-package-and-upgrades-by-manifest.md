# ADR-0026: The substrate ships in the package and upgrades by manifest

**Status:** Accepted

**Date:** 2026-08-13

## Context

A site adopts the container substrate — Containerfile, compose.yaml, the
healthcheck, the db-init bootstrap, the two scripts, the PostgreSQL config
files, the env examples — by copying it out of a podpack checkout, per
creating-a-site.md. Nothing keeps the copy in step afterwards, and that
document has admitted as much from the day it was written.

By the time this record was written the cost had stopped being theoretical.
podpack fixed `scripts/up.sh`'s git-stamp logic on the evening of 2026-08-04;
podpack-demo had copied the file two minutes earlier and still carries the
superseded version. Both real sites regenerated `alembic/env.py` with
`alembic init` and hand-patched it per the docs, losing the canonical file's
reasoning entirely. And the drift is asymmetric in the worst way: a stale
`up.sh` is a bug the site cannot know it has.

ADR-0005 rejected scaffolding for *templates* precisely because copies drift
— "the floor becomes a snapshot taken at creation, and every site drifts from
it separately." The substrate is the one place scaffolding was accepted
anyway, because there is no runtime loader that can serve a container build:
compose reads compose.yaml from disk before any Python runs, and a
Containerfile cannot be resolved out of an installed package at image-build
time. Accepting the copy without the mitigation ADR-0005's rejection implies
is what this record ends.

Two facts about real sites constrain any mechanism. holdenweb.com's
`scripts/` directory holds two substrate files beside eight personal scripts,
seven of them untracked — so anything that sweeps directories can destroy
work that git cannot restore. And sites legitimately edit some of what they
copied: holdenweb rewrote `.env.example`'s prose wholesale and appended its
own secrets to `secrets.env.example`. Those edits are ownership, not damage.

## Decision

The canonical substrate lives inside the package at
`src/podpack/substrate/data/**` and ships in the wheel, so a site's locked
podpack version and its substrate travel together. A console script —
`podpack substrate init | upgrade | status | diff` — installs and maintains a
site's copy.

The engine walks an explicit manifest, never the filesystem (ADR-0004's
explicit-declaration rule, applied to files). Every file has a class:

- **Managed** files (verbatim, or rendered from the site's recorded
  parameters — the Containerfile's factory line is the only render) carry a
  baseline in a committed `substrate.json`: the sha256 of **what podpack
  rendered**, never of what the site has. Upgrade is a three-way comparison
  per file: clean and upstream changed → overwrite; site-edited and upstream
  unchanged → keep, and say so; both changed → write `<file>.new` beside it,
  touch nothing, and exit nonzero until the site resolves it with
  `--take-upstream` or `--keep`. Nothing is ever clobbered.
- **Configuration** (`.env.example`, `secrets.env.example`, and a real
  `.env` where present) follows the site owner's stated policy: once
  installed it changes **only by the addition of new parameters**. Upgrades
  append variables podpack has never delivered to this site — recorded per
  file, so a variable the site later deletes is never pushed back — and
  never modify an existing line. The real `secrets.env` is never written at
  all: a newly-required secret is reported as a fact, because an appended
  lab default in that file is a weak credential on its way to production.
- **Seeded** files (a `.gitignore` that covers `secrets.env`, a README stub,
  `alembic/versions/.gitkeep`) are written once if absent and never touched
  again — ADR-0008's semantics, for files that become the site's on
  delivery.

The alembic skeleton (`env.py`, `alembic.ini`, `script.py.mako`) is managed;
`alembic/versions/` is the site's history and out of scope, as are
`config/app.toml`, `pyproject.toml`, the lockfile, `src/**`, and everything
in `scripts/` the manifest does not name.

A site-edited managed file is reported, not warned about (ADR-0023): editing
your own substrate is a legitimate arrangement, and the report's job is to
keep the fact visible, not to nag it away.

podpack's own repository root is a rendered instance of its packaged
substrate — the first consumer of its own command — and a test pins the two
byte-identical, so the canonical tree cannot drift from the lab that
exercises it.

## Consequences

The sync engine is generic — manifest, baselines, three-way rules; nothing
substrate-specific — because the same shape is the answer to ADR-0008's
deferred app-upgrade gap: an app's shipped static data wants exactly
"update what the host hasn't edited, keep what it has, never touch dynamic
data". That second consumer is deliberately not built yet.

Costs, honestly: `substrate.json` is a new committed artifact sites must not
hand-edit; the wheel grows by the substrate's size; upstream improvements to
the *prose* of configuration files never reach existing sites (the append
rule delivers parameters, not paragraphs); and a conflict asks a human to
read a diff — by design, but it is still a stop.

`creating-a-site.md` Part 2 collapses from three `cp` commands and a table of
hand-edits to one command, which removes the copy-step errors the document
currently warns about (podpack-demo's stale `up.sh` among them, and its
`secrets.env.example` still carrying another site's password strings).

## Alternatives considered

- **Keep documenting the copy.** The measured drift is the argument against;
  the demo following the document faithfully still ended up stale.
- **git subtree / submodule.** Couples a site's history to podpack's and
  drags the whole repository where eight files are wanted; submodules add a
  second checkout step that rootless deployment hosts make painful; neither
  can render the one parameterised line or express "seeded once".
- **Scaffolding tools (copier, cruft, cookiecutter+).** Solve template
  update generically, but add a dependency and a template language for what
  is one token in one file — and their update model rewrites files the site
  owns, exactly what the configuration policy forbids.
- **Discovery by scanning the site tree for podpack-shaped files.** Rejected
  for the same reason ADR-0004 rejected scanning for apps: inference where
  declaration is available, and holdenweb's mixed `scripts/` shows the blast
  radius of guessing wrong.
- **Making compose/Containerfile resolvable at runtime** (generate on
  boot). The files are needed *before* any podpack code runs; a generator
  would itself need installing — the same problem, moved.
