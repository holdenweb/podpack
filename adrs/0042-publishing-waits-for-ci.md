# ADR-0042: Publishing waits for CI

**Status:** Accepted

**Date:** 2026-11-19

## Context

podpack and its apps publish to PyPI from a GitHub workflow, `publish.yml`,
when a release tag is pushed, authenticated by trusted publishing. Each also
runs `ci.yml` on pushes to its branches. Until this record the two were
unconnected. The release push — the release tool's `release --push`, one atomic
push of branch and tag (its ADR-0019) — started both in the same minute, and
nothing in `publish.yml` waited for CI or read its result. It carried a gate of
its own instead, and that gate was a subset of CI's: the tag-matches-version
check and `pytest`. CI also ran mypy and, in podpack, the substrate check and
the migration test that needs a real PostgreSQL.

podpack r0.10.1 went through the gap. On 2026-11-17 CI on `cd4481e` failed its
substrate check — the repository root's record had not been carried forward —
while the publish run on the `r0.10.1` tag, started in the same minute, passed
its own `pytest` and uploaded. CI on main had been failing since 2026-11-11.

A second route bypassed CI altogether: `tools/publish.py`, in podpack and as
byte-identical copies in six apps, uploaded from a laptop with a PyPI token.

The release tool already gates its own GitHub Release on its tests and a tag
check (its ADR-0020). Its ADR-0022 puts the line at the tag: the tool owns
everything up to the pushed tag, the remote everything after, and a workflow
acting on a release tag must apply at least the checks its `verify_tag.py`
makes. The estate's shell comparison applied one of them: it let a `.dev` or
`+local` version through. The tool's backlog also asked that this second,
parallel release path be reconciled with it, or the division of labour written
down, before either changed again.

## Decision

**A release is published only if the tagged commit passes the repository's own
CI.** `publish.yml` runs `ci.yml` as a job — `uses: ./.github/workflows/ci.yml`,
made possible by adding `workflow_call` to `ci.yml`'s triggers — and the publish
job `needs:` it. What a release must pass is therefore what CI runs, by
construction, and the publish job's own `pytest` step goes.

**The tag is checked by the release tool's own `verify_tag.py`**, fetched from
github.com/holdenweb/release at a pinned commit (`d82a039`) and run with
`packaging`: the tag must name the declared version as a string, and a `.dev` or
`+local` version is refused. One definition of the check, tested where it lives.

**`ci.yml`'s concurrency group names the workflow:**
`ci-${{ github.workflow }}-${{ github.ref }}`. Called from `publish.yml`,
`github.workflow` is the caller's name, so a release's CI run has a group of its
own. Under the old `ci-${{ github.ref }}`, a standalone CI run on the same tag
would share the group, `cancel-in-progress` would let either cancel the other,
and a cancelled CI skips the publish with no error anywhere. podpack's `ci.yml`,
which ran on every push including tags, now runs on branches only, so a tag
push no longer runs CI twice.

**`publish.yml` is the only route to PyPI.** `tools/publish.py` is retired, from
podpack and from the six apps that carried copies.

**The division of labour is the release tool's ADR-0022, unchanged.** `release`
cuts the release and pushes the tag; each repository's `publish.yml` runs that
repository's CI on the tagged commit, verifies the tag, builds into a clean
checkout and publishes by trusted publishing. Neither reads the other's
configuration, and the tag is the whole of the message between them.

## Consequences

**Nothing reaches PyPI from a tag whose commit fails CI.** Proved on
podpack-qrcode on 2026-11-19, with two throwaway tags that could never have
published:

- `r0.0.0+gate1`, on a commit adding a deliberately failing test: CI failed in
  Tests, and the publish job was skipped (run 44543054831).
- `r0.0.0+gate2`, on main: CI passed, and the tag check refused the tag —
  "names '0.0.0+gate2', but pyproject.toml at that commit declares '0.2.3'" —
  so nothing was built (run 44543102730).

PyPI was unchanged. Locally, in a scratch project declaring `0.11.1+dev`, the
old shell check passed `r0.11.1+dev` and `verify_tag.py` refused it.

**A failed release leaves a tag with nothing behind it.** The tag is pushed
before CI judges its commit, so a red release is a tag on GitHub naming a
version PyPI never received. Delete it, on GitHub and locally, fix, and cut the
release again; the version is still unused. Tagging only green commits — the
release tool waiting for CI before it tags — is the second layer, not taken
here: it moves verification across ADR-0022's line, and needs a record of its
own in the release tool.

**The pin is moved by hand.** Eight publish workflows name `d82a039`, so a
change to `verify_tag.py` reaches them only when all eight are edited.
Deliberate: a release's checks should not change underneath it.

**Publishing needs raw.githubusercontent.com.** The tag check fetches its script
at publish time; an outage that stops the fetch stops the run, which fails safe.

**A release now runs the whole of CI.** podpack's releases get the PostgreSQL
migration test and the substrate check, which they never had; the apps' get
mypy. A release takes as long as CI does.

**The PyPI tokens the laptop route used are now pointless**, and are Steve's to
revoke; until they are, that route still exists outside the repositories.

**podpack-lablog has no remote yet.** Its workflows carry the change, and take
effect when it gets one.

## Alternatives considered

**`workflow_run`: publish triggered by CI's completion.** It runs the default
branch's copy of the workflow rather than the tag's, carries the tag only
indirectly, and splits one decision across two files. `workflow_call` runs the
tag's own `ci.yml` as a job of the publish run.

**Repeating CI's steps in `publish.yml`.** What it had, in part — and the subset
is how r0.10.1 got through. Two lists drift.

**Vendoring `verify_tag.py` into each repository.** No fetch, but eight copies
to keep in step with the one that is tested.

**Keeping `tools/publish.py` as an emergency route.** An ungated route makes the
gate a default rather than a guarantee.

**Tagging only green commits.** The tidier end state, deferred as above.
