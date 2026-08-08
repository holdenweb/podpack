# ADR-0000: Template

**Status:** Template — copy this file, do not edit it in place.

**Date:** yyyy-mm-dd

## Context

What was true that forced a choice. The pressure, not the answer: what did not
work, what had to be reconciled, what would have gone wrong. Enough that a
reader who was not there can feel the problem before reading the decision.

## Decision

One paragraph, in the present tense, saying what we do. Not "we will" — an
accepted ADR describes how the system is.

## Consequences

What follows, including what got worse. A record with no costs in it is a
record nobody will trust. Say what this forecloses and what it makes someone
else's problem.

## Alternatives considered

What else was on the table and why it lost. This is the section that stops a
decision being reproposed in six months.

---

## How to use these records

An ADR is **immutable once accepted**. When a decision changes, write a new
record that supersedes it and mark the old one `Superseded by ADR-nnnn`; do not
rewrite history, because the reasoning that was true at the time is the thing of
value.

Statuses in use: `Accepted`, `Deferred` (a decision to not decide yet, with the
trigger to watch for), `Rejected`, `Superseded by ADR-nnnn`.

**What lives where.** These three overlap and would otherwise drift:

| | Answers | Changes when |
| --- | --- | --- |
| `README.md` | how do I use it? | the interface does |
| `claude.md` | where is the project up to? | the work does |
| `adrs/` | why is it like this? | never — superseded instead |

**Date** is when the record was written, not when the decision was taken.
ADR-0001 to ADR-0023 were all written on 2026-08-08, reconstructing decisions
made over the preceding days from the commits that carry them — so several cite
evidence from before their own date, and one or two from after. Where the timing
matters, the Context says so.
