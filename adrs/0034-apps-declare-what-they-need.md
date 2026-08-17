# ADR-0034: Apps declare what they need, and podpack checks it

**Status:** Accepted — extends
[ADR-0032](0032-tables-are-claimed-not-prefixed.md), whose `owns_tables` this
renames and corrects

**Date:** 2026-08-17

## Context

ADR-0032 gave an app `owns_tables` so it could say that an unprefixed table
name was deliberate. The name was wrong, and the model under it was wrong in
the same way: it recorded **one owner per table**, so the second app to
declare `user` replaced the first in `table_owners` silently. Nothing detected
that, because sole ownership was an assumption nobody had checked rather than
a fact anybody had established.

Ownership is also the wrong question. An app that joins to `user` needs it as
genuinely as the app that defines it, and several apps needing one table is
the ordinary case, not a conflict.

Meanwhile the declaration was recorded and never *read*. An app declaring a
table that no installed app defines produced a site that booted, served every
page, and failed at the first query — a stated requirement nobody checked,
which is the same shape as the `SECURITY_PASSWORD_SALT` failure that prompted
the boot-time secrets check days earlier.

## Decision

Three fields on `SiteApp`, all declarations, all checked:

| | |
| --- | --- |
| `needs_tables` | tables this app reads or writes but does not define |
| `defines_tables` | tables it defines that attribution cannot see |
| `needs_secrets` | environment variables it cannot run without |

and two records in `PodpackState`, kept apart because they answer different
questions:

    defined_by: dict[str, str]        one app per table
    needed_by:  dict[str, set[str]]   every app that needs it

`defined_by` is read from SQLAlchemy's mapper registry, which is a fact.
`defines_tables` exists only for what that registry cannot see — a table with
no mapped class, such as an association table built with a bare `db.Table`.

At boot, after the apps are installed, **a table needed by something and
defined by nothing is a failure**:

```
this site is missing a table that nothing installed defines:
'someone_elses_notes', needed by 'reports'. Either add the app that defines
it to `[site] apps`, or stop installing the app that needs it.
```

A failure rather than a warning, matching what an unknown name in `apps`
already does: a site missing a dependency cannot do what it was configured to
do, and saying so quietly leaves it to be found by a visitor.

## Consequences

App dependencies are now a real concept, mediated by the **schema** rather
than by imports. That is deliberately looser than a package dependency: an app
names a table, not a module, so it stays installable without importing its
neighbours, and the coupling is the shared `db.metadata` namespace that
actually breaks (ADR-0009).

The check runs against what is *declared*, never against the database.
Whether a table has been created is alembic's business and `/_status`'s
`unclaimed.tables`; whether anything even claims to define it is answerable
statically, at boot, which is earlier and cheaper.

`/_status` now reports `defines_tables` and `needs_tables` per app instead of
one merged `tables`, because "what would this app take with it if uninstalled"
and "what would it break without" are different questions.

Removal is the loose end. Drop the app that defines `notes` while another
still needs it and the site now refuses to start, which is right; but
`unclaimed.tables` will not flag the orphaned table, because something still
declares a need for it. Arguably correct — the data is still wanted — and
worth revisiting when somebody actually hits it.

## Alternatives considered

- **Deducing ownership from the table's name prefix.** Raised, and it fails
  exactly where it would be needed: the tables this framework most needed to
  attribute are `user`, `role` and `roles_users`, which are flask-security's
  names and carry no prefix at all. Prefixes are not unique either — apps
  called `note` and `notes` both prefix `notes_archive` — and the convention
  is a warning an app may ignore. Where the prefix *would* be a fair guess we
  already have a better answer from the mapper registry. It remains the only
  clue available for an **orphan** table that nothing defines, and is worth
  offering there as a hint some day.
- **Warning instead of failing.** Rejected on the evidence of this session: a
  warning that fires on a permanent, correct condition is noise people learn
  to scroll past, and a warning about a genuinely broken site is one nobody
  reads until the site is broken.
- **Checking the database rather than the declarations.** Different question,
  answered elsewhere, and it would make `create_app` depend on a reachable
  database at import time.
- **Full package-level dependencies between apps**, with version constraints.
  Much heavier, and it would couple apps to each other's code rather than to
  the one namespace they genuinely share.
