# ADR-0012: Build the image in two stages

**Status:** Accepted

**Date:** 2026-08-08

## Context

`git` went into the image for a reason (eae4ac8). uv shells out to a real `git`
to fetch a dependency locked to a git source, and `python:3.12-slim` has none,
so an app installed straight from its repository — the ordinary case for one not
published to an index — fails the build with "Git executable not found", which
names nothing that would lead anyone to the Containerfile.

It is expensive groundwork. Measured, `git` is 104MB, the `uv` binary 47MB, and
uv's download cache, populated as a side effect of `uv sync`, about 44MB: half
of a 398MB single-stage image, spent on three things needed to *build* the
virtual environment and on none of them to run it.

Deleting them in a later `RUN rm` does not help. The layer that installed a file
still carries it and a deletion only records another layer on top, so the image
gets slightly bigger. Not shipping something is the only way to not ship it.

## Decision

The Containerfile builds in two stages (cdbae5a). The builder installs git,
takes `uv` from its own pinned image, and runs `uv sync --frozen` twice — once
for dependencies alone, keyed on the lockfile, then again for the project, so
editing source does not reinstall the world. The runtime
stage starts from a clean `python:3.12-slim` and copies the finished `.venv`,
`src/`, `alembic.ini` and `alembic/`, and `container/healthcheck.py` — 203MB,
and no toolchain.

`.dockerignore` belongs to this change rather than to housekeeping. `COPY src/`
and `COPY alembic/` were sweeping up the host's `__pycache__`, so the image
shipped bytecode compiled on a laptop, including valid `.pyc` for migrations
long deleted, loaded in preference to the source actually present. The `**/` in
`**/__pycache__` is load-bearing: a bare `__pycache__/` anchors to the context
root and would exclude nothing while looking correct.

## Consequences

Both stages set `WORKDIR /app`, and not by coincidence. A venv is tied to its
absolute path twice over: console-script shebangs carry the interpreter path,
and the project is installed into it as an editable pointing at
`<workdir>/src`. Built under one directory and copied to another it gives
`gunicorn: not found` with exit 127, which reads like a PATH problem, *and*
`ModuleNotFoundError: No module named 'podpack'`. Neither message mentions the
venv. That editable is also why the runtime stage copies the source: the venv
alone is not a complete installation.

The runtime image cannot install, fetch or resolve anything. A dependency
problem is diagnosed by rebuilding, not by shelling in.

It cannot identify itself either: `.git` is excluded from the context and the
runtime stage has no git, so the commit arrives as `ARG GIT_SHA` from
`scripts/up.sh` and reads `unknown` when anyone builds by hand.

The `WORKDIR` trap mostly fails loudly — `COPY --from=builder /app/.venv` cannot
find its source and the build stops. Only setting the two to *different* values
yields the quietly broken image, which is the case that reaches a host.

Installing an app from outside the repo stays two operations: `uv add` plus a
rebuild puts the distribution in the image, and a line in `app.toml` enables it
([ADR-0004](0004-app-list-is-configuration.md)). Only the second is the
config-and-restart the framework advertises.

## Alternatives considered

**Single stage, deleting the toolchain afterwards.** The obvious move, and it
shrinks nothing.

**Not installing git at all**, since nothing is locked to a git source yet. The
failure it buys back lands on whoever installs the first such app, in a message
about a missing executable rather than about their app.

**Building the venv on the host and copying it in.** The host venv is macOS and
carries host absolute paths, so it is wrong twice for a Linux image — which is
why `.dockerignore` excludes `.venv/`.
