# ADR-0035: Apps declare what is theirs to back up, and podpack backs it up

**Status:** Accepted — pays the debt
[ADR-0015](0015-postgresql-stays-in-a-container.md) recorded, and extends
[ADR-0034](0034-apps-declare-what-they-need.md)

**Date:** 2026-08-20

## Context

ADR-0015 put PostgreSQL in a container and named the price in the same breath:

> Backups, tuning and patching are the site's problem. `scripts/` contains
> `prepare-host-dirs.sh` and `up.sh` and no `pg_dump` anywhere — running the
> database has not yet been paid for in the one place it will be missed.

"Has not yet been paid for" is the language of a debt, not of a principle. A
year of sites each writing their own backup script is what that debt looks
like when it is serviced rather than repaid, and three of them now exist in
`~/sites` with different capabilities and no shared fixes.

Two things made the interest visible.

**The apps differ wildly, and nothing recorded it.** Measured on the live
holdenweb.com: `pages` holds 1.2 MB across 20 files — the site's entire
content, edited over ssh, with no write route, so the app cannot regenerate a
byte of it — while `qrcode` and `pp_pdf` hold zero bytes each, because both
stream their output and store none of it. `main` holds 8 KB of an untracked
`.DS_Store` that permanently disarmed its seeding. The one backup script in
existence archived all four identically, and would have said nothing at all if
`pages` had been empty.

**A site running MongoDB had no backup of it whatever.** The script hardcoded
`pg_dump`, because the person writing it had PostgreSQL in mind. Nothing
anywhere knew that a store implies a way to dump it.

## Decision

Two declarations, on the two things that actually vary, and **a simple app
declares neither.**

Almost everything needed is already known. [ADR-0007](0007-per-app-data-and-log-directories.md)
fixes an app's files at `<data root>/<name>` and creates that directory for
every app; the mapper registry already answers which tables an app defines,
as a fact rather than a claim ([ADR-0034](0034-apps-declare-what-they-need.md)).
So a tool that walks the registry backs a storing app up correctly with no
change to that app at all — which is the whole of "simple apps are included
automatically", and it needed no new API.

`SiteApp.backs_up` exists for what looking cannot establish:

```python
site_app = SiteApp(blueprint=bp, backs_up=Backup(data=False))
```

Chiefly that **an empty directory is ambiguous**. `podpack-qrcode` is zero
bytes because it is stateless; a mount that never arrived is zero bytes too,
and from the outside they are identical. `Backup(data=False)` is a claim
somebody can be held to. `None` is nobody having said, which podpack resolves
as *keep everything, and report that nobody vouched for it* — backing up more
than necessary costs disk, backing up less costs the data.

`Backup` also carries `excludes` (derived subtrees a restore can rebuild),
`extra` (state outside the app's own directory — the escape hatch, and the one
to be suspicious of) and `reseedable` (whether a fresh install would
regenerate this, which is false for anything a person can edit, because
`_seed_data` fires only into an empty directory).

`CoreService` gains `dump`, `restore`, `verify` and `dump_file`. Declared per
service rather than derived, and the catalogue's own rule — declare the name,
derive the rest — is exactly why that needs saying: nothing turns `postgres`
into `pg_dump`, or `mongodb` into `mongodump`. They join `uri_env` and
`init_dir` as exceptions carrying their reason.

**podpack plans; the substrate scripts execute.** `podpack backup plan` prints
what a backup must include and does none of it. It reads which stores are
running from the `PODPACK_SERVICE_<NAME>` markers each overlay stamps into the
container — what compose actually merged, rather than what a config file
claims — and it emits app *names* and subpaths, never absolute paths, because
it runs where the data root is `/var/lib/<site>/apps` and is consumed where it
is `$HOST_DATA_DIR/apps`.

## Consequences

**A wrong backup declaration warns; it does not refuse to boot.** This is the
one declaration on `SiteApp` treated that way, and the departure is
deliberate. A missing table breaks the site now; a wrong backup declaration
breaks nothing until somebody tries to restore, so refusing to start would
trade a real outage for a hypothetical one. An app claiming statelessness
while its directory holds files is warned about at boot and reported on
`/_status`, which is what podpack already does for unclaimed tables and
unclaimed directories: report, never tidy away.

An app that declares nothing is never warned about. Silence is a legitimate
state, and a warning at every boot for the ordinary case is how people learn
to stop reading the boot log.

**Backups become secret-bearing.** `secrets.env` is archived, because
ADR-0013 split the environment precisely so that a restore would be *copy the
one file, edit the other* rather than a hand-edit under pressure — and a
backup that omits it leaves a site that starts, serves, and cannot verify a
single stored password. The cost is that every backup directory is a
credential store: mode 0700, refused inside the working tree, and the
operator's problem to keep off shared disks.

**Logs are not backed up.** They are evidence, not state; a site restored
without them is whole.

**A site's backup now changes when its app list does**, with no edit to any
script — which is the same property `[site] apps` already has for routes,
templates and migrations.

## Alternatives considered

**Leave it to sites, as ADR-0015 said.** What has actually happened: three
divergent scripts, one of which silently omitted a whole store. The framework
knows which apps are installed and what each keeps; a site cannot know either
thing without asking it.

**A `backup_extra()` method rather than a field**, matching the `healthz` /
`status` precedent of [ADR-0030](0030-apps-report-health-and-status-by-overriding.md).
Right if an app ever needs its own state to enumerate what it keeps — none of
the five real apps does. Deferred rather than rejected: if the case appears,
ADR-0030's argument says it arrives as a method, not as a callable field.

**Refusing to boot on a contradicted claim**, consistent with every other
declaration. Rejected above: the failure it describes is in the future, and
the outage would be now.

**Deriving the dump command from the service name.** `pg_dump` from
`postgres` requires knowing that PostgreSQL's tools are prefixed `pg_` and
that the service is named for the product rather than the tool. A rule with
one exception per member is not a rule.

**Excluding secrets from the archive**, keeping backups non-sensitive. It
makes a restore need two sources, and the one you must not forget is the one
nothing reminds you about. Encrypting instead was rejected for the reason
ADR-0013 rejected a secret store: a key is a second thing to back up.
