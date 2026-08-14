# ADR-0031: The CLI keeps its command groups

**Status:** Accepted — records an alternative that was built and rejected

**Date:** 2026-08-13

## Context

`podpack --help` listed one group, `substrate`, and stopped. The five
commands anybody actually types were therefore invisible from the only place
a reader looks first, and `podpack init` — the obvious next guess — is an
error. Steve read the source, found `add_parser("init", ...)` and the rest,
and concluded the commands were unreachable. That is a fair reading of what
the help said.

The obvious fix was to flatten: `podpack init`, `podpack status`,
`podpack upgrade`, `podpack diff`, `podpack services`, with no group at all.
It was built on a branch, tests and all, and the argument for it was decent:
the group had never gained a sibling, and the one candidate — creating a
user — had just been shown to belong to the Flask CLI instead, because
anything needing a *running application* gets `--app` for free.

## Decision

The groups stay. `podpack substrate init` remains the spelling.

What was actually wrong was the help text, and that is fixed independently:
`podpack --help` now lists all five commands in full, with their
descriptions, so nothing is hidden behind a group name.

The reason to keep the nesting is not the current shape of the tool but the
one being kept open. A flat namespace makes every future command compete for
a bare verb — `init` for what? — and renaming it later, once other people
type it, costs far more than the extra word does now. `substrate init` says
what is being initialised, and leaves `podpack <group> <verb>` free for
whatever the next group turns out to be.

## Consequences

One more word to type, for ever, on the commands run most often.

In exchange, the vocabulary stays open: a second group can arrive without
renaming anything and without a period where two spellings both work. The
cost of the wrong choice is asymmetric — flattening now and regretting it
means breaking a published interface, while keeping the nesting and never
using it means having typed nine extra characters.

The distinction the flat branch surfaced is worth keeping even though its
conclusion was not: **this script is for what needs no running application,
and anything that needs one is a Flask command.** That line is now in the
script's own docstring, and it is what sent user administration to
`flask users create` rather than into a second group here.

## Alternatives considered

- **Flattening** — built as the `flat-cli` branch and rejected here rather
  than in the abstract, which is why this record exists at all. The branch
  is kept: it is the cheapest way to see exactly what the change costs if
  the question comes back.
- **Flatten with `substrate` retained as a hidden alias.** Two spellings in
  the wild, one undocumented, for as long as anybody's fingers remember —
  and it forecloses nothing that keeping the group does not.
- **Leaving the help as it was.** The complaint that started this was real,
  and a reader concluding the commands do not exist is the strongest
  possible evidence that a help text has failed.
