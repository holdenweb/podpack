# ADR-0041: Sending mail is an app

**Status:** Accepted — supersedes the mail clause of
[ADR-0025](0025-the-site-wires-its-own-extensions.md)

**Date:** 2026-11-02

## Context

ADR-0025 kept mail in the site's own `init`. flask-mailman registers no
blueprint, an app is built around one (ADR-0002), and a shim inventing a
blueprint would have claimed a name, a template namespace and two directories
for nothing.

Two things changed.

**Every site is now to send mail.** The contacts app is meant for every site,
and ADR-0040 gives each site a mailbox and each deployment an address. Wiring
the transport in each site's `init` would be ADR-0033's argument one layer down:
the same twenty-odd lines in every site. holdenweb.com's copy also carried a
defect its own record got wrong (its HW-0030). That record said a deployment
could override the settings from the environment, but only the password was
ever read from there.

**The objections did not survive a reading of the code** (the
contacts briefing, 2026-10-14, re-read for this record on 2026-11-02):

- *flask-security needs its mail transport before the apps install.* It does
  not. podpack reads `PODPACK_MAIL_UTIL_CLS` at `auth.install`, which needs only
  the class. flask-security finds the transport at send time, with
  `current_app.extensions.get("mailman")`. So an app's `init`, which runs after
  login is installed, is soon enough.
- *A mail app's blueprint would have nothing to do.* It has a job. Flask
  registers a blueprint's own `cli` commands under the blueprint's name, so an
  app named `mail` carries `flask --app <site> mail check`, the command
  holdenweb.com's operations already document.
- *Without a mail app, login would fail silently.* The briefing said
  flask-security's lookup had no `else`. In 5.8.2 and 5.9.2 it does: `raise
  ValueError("No email extension configured")`. So the failure is loud, but it
  comes at the first registration or password reset, in front of a visitor.

## Decision

**A site's mail transport is an app: `podpack-mail`, app name `mail`.**
- It configures flask-mailman from `[apps.mail]` and the deployment's
  environment (ADR-0040).
- It declares `MAIL_PASSWORD` in `needs_secrets`.
- It carries `mail check` on its blueprint.
- It reports its settings in `status()`.
- It refuses to start a site whose SMTP settings are incomplete or ask for
  STARTTLS. flask-mailman 1.1.1 verifies the server's certificate on implicit
  TLS but not on STARTTLS.

**What a failure says stays the site's.** podpack hands
`PODPACK_MAIL_UTIL_CLS` to flask-security before any app installs, so an app
could not set it in time. The site names that class in its own `init`, as
ADR-0025 said, and HW-0014 is holdenweb.com's.

**podpack refuses to start a site whose login sends mail when nothing is
installed to send it.** After the apps install, `auth.check_mail` compares two
things:
- the flask-security features that send mail, as a table read off its
  `send_mail` call sites;
- the transports registered in `app.extensions`, either `mailman` or `mail`.

## Consequences

**An app with no routes.** The blueprint exists to name the app (ADR-0003) and
to carry its commands. Its data directory is declared stateless with
`Backup(data=False)` (ADR-0035), so the empty directory ADR-0025 worried about
is accounted for rather than reported as unvouched for.

**The boot check names every feature at once** and leaves a site that sends no
mail alone. A site may still wire its own transport in its own `init`, because
any registered transport satisfies the check. That is how holdenweb.com passes
it until it cuts over.

**The table has to follow flask-security.** A release that adds a mailing
feature needs a row. The tests keep rows and cases in step, but they cannot
discover a new feature on their own. The table was read from 5.8.2, which is
what podpack's own environment runs, while holdenweb.com runs 5.9.2. Re-read
the call sites on an upgrade.

**A laptop prints instead of sending.** The backend is derived from
`PODPACK_ENVIRONMENT` as the proxy hop count is (ADR-0039): `console` when
local, `smtp` otherwise. Anyone used to a local run sending real mail now has
to set `MAIL_BACKEND=smtp`.

**An app still cannot declare that it needs another.** An app that sends its
own mail should look up `mailman` at send time, degrade without it, and say in
its README that the site should install podpack-mail. Only login's need is
checked, because login is podpack's (ADR-0033).

**`Message-ID`s stay flask-mailman's**, built from the container's hostname.
That holds until the receiving half, planned for the same distribution
(ADR-0038), mints its own.

## Alternatives considered

**Keep mail in each site's `init`** (ADR-0025). That means twenty-odd lines per
site, with the defect HW-0030 recorded, and nothing for an app in another site
to rely on.

**Put mail in podpack core, as a service.** This is a category error. A
service is a backing store with a container (ADR-0028), and mail is an
outbound client. The briefing proposed this on 2026-10-12 and reversed it two
days later.

**A `requires_apps` declaration**, with which login or an app would name the
mail app it needs. Steve decided on 2026-10-12 not to build that yet. The check
here is narrower: it covers only what login needs, which podpack knows because
login is podpack's.

**Check at the first send instead of at boot.** That is what flask-security
already does, and it does it in front of a visitor.

**Support STARTTLS.** On that path flask-mailman 1.1.1 passes `context=None`
and verifies no certificate. Implicit TLS on port 465 is verified, and every
provider the estate uses offers it.
