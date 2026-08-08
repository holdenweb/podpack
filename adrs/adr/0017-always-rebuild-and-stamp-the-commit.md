# ADR-0017: Always rebuild, and stamp the commit into the image

**Status:** Accepted

**Date:** 2026-08-08

## Context

Framework source is baked into the image. The runtime stage copies `src/` in,
because [ADR-0012](0012-two-stage-image.md) deliberately leaves no toolchain
behind that could install it later. So editing `src/podpack/` and then reaching
for `podman compose restart` brings back the *previous* build: the site goes on
behaving like the last commit, and the symptom — a change that simply does not
appear — points nowhere near the cause.

What made the trap sharp rather than merely annoying is that restarting is
sometimes right. `config/app.toml` and the PostgreSQL config files are mounted
read-only from the host, so editing those really is a restart-only change, and
the habit that works there fails silently one directory over.

Nor could the running container answer "is this the code I am looking at?".
File timestamps only approximate it, and in the wrong direction: the running
image is routinely *older* than the commit containing its code, because building
precedes committing.

## Decision

The suite comes up through `scripts/up.sh`, which always rebuilds and records in
the image the commit it was built from; `/_status` reports that as
`build_commit`, with a `-dirty` suffix for an uncommitted tree. Rebuilding
unconditionally removes a whole class of mistake rather than documenting it, and
the stamp turns "is this running the code I am looking at?" into something you
can read rather than infer.

The commit has to arrive as a build argument, because the image cannot work it
out for itself: `.git/` is excluded from the build context and the runtime stage
has no git.

## Consequences

Every start pays a rebuild: about six seconds when nothing has changed, because
layers are content-addressed and an untouched file invalidates nothing. That is
cheaper than the puzzlement, but it is charged on the config-only edits that
needed no build at all, and the fast path now exists only by bypassing the
script the README recommends.

The stamp is only as honest as the entry point. Building by hand, or running
`podman compose up -d --build` without `GIT_SHA` in the environment, records
`unknown` — and since both services share one image tag, that replaces a
stamped image rather than sitting beside it.

`-dirty` compares tracked files only. A new module under `src/` that has never
been `git add`ed is copied into the image and the stamp still reads clean, which
is the one case where `build_commit` lies.

`/_status` now discloses the commit to anyone who can reach it, alongside the
database name, user and host paths it already reported.

## Alternatives considered

**Document the trap.** It is documented, in the README and in the project brief,
and documentation only helps the reader who remembers which edits need which
command at the moment they are making one. Removing the choice costs six
seconds.

**Compare timestamps instead of stamping.** Building precedes committing, so the
comparison is systematically wrong in the ordinary case and gives a confident
answer either way.

**Work the commit out inside the build.** That needs `.git` in the build context
and git in the runtime stage. The first is excluded because the context was 90MB
of database and host venv without it; the second was removed on purpose by
[ADR-0012](0012-two-stage-image.md).
