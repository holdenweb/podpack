# ADR-0032: Tables are claimed, not prefixed

**Status:** Accepted

**Date:** 2026-08-15

## Context

`db.metadata` is the one namespace podpack does not divide by app name.
Templates, data directories, log directories and config sections all carry
it; table names are flat, because there is one metadata and one migration
history (ADR-0009). A second app claiming `user` does not shadow the first,
it stops the site booting.

So the registry warns when an app declares a table its own name does not
prefix, at the author's site rather than at the collision — by the time two
apps collide, the person who could fix it is not the one reading the error
(ADR-0003's reasoning, applied to tables).

On holdenweb.com that warning fires twice on every command, including
`flask roles --help`:

```
WARNING: app 'main' declares the table 'role', which its own name does not prefix.
WARNING: app 'main' declares the table 'user',  which its own name does not prefix.
```

The obvious reading is that the site should prefix them, and the obvious
next step is a migration to `main_user` and `main_role`.

## Decision

An app may declare the table names it claims deliberately:

```python
site_app = SiteApp(
    blueprint=blueprint,
    owns_tables=frozenset({"user", "role", "roles_users"}),
)
```

A claimed name is not warned about, and — the reason to prefer this to a
mute — it is *recorded*. It reaches `table_owners`, so `/_status` reports
which app answers for the table and a later clash can name the incumbent.

`/_status` also grows `unclaimed.tables`: tables in the database that no
installed app answers for, read from the database rather than from
`db.metadata`, exactly as the data and log roots are read from disk.

## Consequences

The warning becomes worth reading. It now fires only on names nobody has
thought about, which is what it was always for — a warning that a site
learns to scroll past has negative value, and this one was on its way there.

Ownership stops having holes. `roles_users` is built inside flask-security
rather than in the site's own module, so attribution by defining module
never saw it: it was in the schema, owned by nobody, invisible to `/_status`
and unnameable in a clash message. Nothing detected that until
`unclaimed.tables` was built and the table appeared in it.

An app can now claim a name it has no business claiming, and podpack will
believe it. That is the same trust the framework already extends to
`url_prefix` and to every table an app defines outright; the site's `apps`
list is the control, as it is for everything else.

## Alternatives considered

- **Prefixing the tables** — proposed, and rejected for four reasons that
  compound. The names are not the site's to choose: flask-security derives
  `user` and `role` from its own mixins, and its datastore, its
  documentation and its join table assume them. It is a migration over live
  user rows for no functional gain. It cannot be finished — `roles_users` is
  created inside flask-security, so no `__tablename__` in the site reaches
  it. And the rationale does not apply: the warning anticipates *two apps*
  colliding, while these tables belong to the site, of which there is
  exactly one per instance (ADR-0001). The site is the incumbent; a
  third-party app that later declares `user` is the one that must move.
- **Automatic prefixing by the framework**, rewriting `__tablename__` as an
  app is installed. Silently renames a dependency's tables, breaking the SQL
  and the assumptions that dependency ships with, and still cannot reach a
  table built inside one. It would also be a migration on every existing
  site, imposed by a framework upgrade.
- **Suppressing the warning per app**, a boolean rather than a set of names.
  Cheaper, and it buys nothing: the names are what makes the declaration
  useful, and a blanket suppression would hide the next unconsidered table
  the app adds.
- **Leaving it alone.** Tenable while one site has two such tables. It stops
  being tenable at the point the warning is noise on every command, which is
  where it already was.
