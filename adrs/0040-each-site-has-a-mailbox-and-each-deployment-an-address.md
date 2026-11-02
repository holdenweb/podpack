# ADR-0040: Each site has a mailbox, and each deployment an address

**Status:** Proposed — accepted once the sending app exists and a deployment
sends through its own address.

**Date:** 2026-11-01

## Context

Every deployment is now to send *and receive* mail: the contacts app tracks the
threads its enquiries start, and it is meant for every site
([briefing](../briefing-contacts-and-bulletin-board.md), App 1). Until now the
estate could only send.

Sending is the site's business (ADR-0025), and the one deployed site,
holdenweb.com, does it by authenticating to Gmail as a person with an App
Password (its HW-0030). Receiving has never been done (briefing §3), and Google,
the obvious place to do it, fits badly. holdenweb.com's MX is Google, but the
address it advertises is to be a Google Group, which by the briefing's account
the Gmail API cannot read; a mailbox the app *could* read costs a Workspace
seat, and reading it needs a service account with domain-wide delegation.

Two facts about the host changed what was possible.

**Opalstack, which hosts every deployment, creates mail through an API.**
`mailuser/create` makes a mailbox and `address/create` points an address at
one. Both are asynchronous, reporting `PENDING_CREATE` until `READY`. A
mailbox's password is write-only — no read returns it — and tokens are scoped
per resource type through `tokenacl`. All of that is from the published schema
(`my.opalstack.com/api/v1/schema/`, read 2026-11-01); none of it has been
exercised.

**`mail.holdenweb.com` is delegated to Opalstack, which publishes its mail
records itself.** Checked 2026-11-01: holdenweb.com's nameservers refer it to
`ns1`–`ns3.de.opalstack.com`, and it answers with MX
`mx1`/`mx2.de.opalstack.com`, SPF `v=spf1 include:spf.opalstack.com ~all`, a
DKIM key at selector `dkim`, and DMARC `p=none`. A mailbox or an address there
needs no DNS work at all.

## Decision

**Each site has one mailbox, `${SITE}-admin`, in a mail domain delegated to the
hosting provider, and each deployment has an address there that delivers into
it: `${SITE}-stag@` and `${SITE}-prod@`.** `${SITE}` is a two- to
four-character mnemonic chosen for the site. holdenweb.com's is `hw`, so its
mailbox is `hw-admin` and its addresses are `hw-stag@mail.holdenweb.com` and
`hw-prod@mail.holdenweb.com`, whose local parts are the names its two
deployments already have on opal17.

**A deployment logs in as its site's mailbox and sends from its own address**,
so its `From:` is in the delegated domain and aligns by construction. **It
reads only its own mail**, picking it out of the shared mailbox by
`X-Original-To`.

**The apps that send and receive speak SMTP and IMAP, and know nothing of the
provider.** Their settings are the deployment's: the address and the login in
`.env`, the password in `secrets.env` (ADR-0013, ADR-0018).

**Creating a mailbox is host tooling.** `scripts/provision-mail.py SITE` is
built as `configure-host.py` is — standard library only, no podpack import, run
by the operator on the host. It creates the mailbox with a `generated()`
password and then the two addresses, and waits for `READY` with a time limit.
It writes the password to a per-site file at mode 0600 outside any checkout and
never prints it; `configure-host.py` fills the secret from that file when it
writes a new deployment's `secrets.env`. It refuses if the mailbox exists, and
rotating is an explicit `--rotate`. Its API token is read with `getpass`, never
from `argv` or the environment, and can read and write mailboxes and addresses
and nothing else — above all not domains, because Opalstack's domain record
carries the DKIM private key (`dkim_privkey`).

## Consequences

**No DNS work per site or per deployment.** Adding a site is a few API calls
and a wait. The one DNS dependency left is the delegation itself: its NS
records are in holdenweb.com's zone at DNSMadeEasy, so that zone, and what it
costs, stay until it moves as well.

**Verified by hand with `hw`, 2026-11-01** — mailbox and addresses made in the
panel, messages sent with `holdenweb.com/scratch/mailtest.py`:

- from `hw-stag@`, authenticated as `hw-admin` through
  `smtp.de.opalstack.com:465`, to a Google Workspace inbox: SPF, DKIM and DMARC
  all pass;
- a reply arrived with `X-Original-To` naming `hw-stag@`;
- `hw-stag+<tag>@` is delivered, so a token in `Reply-To:` can make a reply
  self-identifying;
- on that message `X-Original-To` dropped the tag and read `hw-stag@`, while
  `Delivered-To` kept it, on the mailbox's own address
  (`hw-admin+tag@imap2.de.opalstack.com`) — so a deployment is known by one
  header and a reply's token by the other;
- a hyphenated mailbox name is accepted, and authenticates.

Not verified: the whole API path — the token's scoping, how long
`PENDING_CREATE` lasts, and which permission yields the IMAP server's id that
`mailuser/create` requires.

**Staging holds production's credential.** With one mailbox per site, staging
can read production's correspondence and can send as production. That is
inherent in the scheme, and acceptable where one person runs both. A site that
cannot accept it gives a deployment a mailbox of its own: an address can
deliver to any mailbox, so the naming survives.

**A rotation reaches every deployment of the site at once.** Changing the
mailbox's password revokes the old one in the same act — there is no revocation
step to forget, as there is for a Gmail App Password (holdenweb.com's HW-0017).
The price is that every deployment of the site fails to send and to fetch until
its `secrets.env` has the new value and its container is recreated, because
that file reaches the container only through `env_file`.

**`secrets.env` is still written once, except at the cut-over.** A new
deployment gets the password from `configure-host.py`. A deployment that
already exists gets it by hand, once, in the file `compose.yaml` says is never
edited.

**The provider lives in one script.** Leaving Opalstack means rewriting the
provisioner, not the apps. But the scheme relies on three behaviours of
Opalstack's mail server: it accepts a `From:` that is one of the mailbox's
addresses rather than its login; it adds `X-Original-To`; and it carries a
`+tag` through to `Delivered-To`. A provider without the first two would need
a mailbox per address.

**It adds a name.** `${SITE}` joins the names ADR-0037 counts, and nothing
derives it from them. It is chosen once per site and appears in every address a
correspondent sees, which is why it is short. Two addresses per site also
assume two deployments; a laptop has none, and what it does about mail is for
the sending app to decide.

**Mail leaves the site's `init`.** ADR-0025 kept mail in the site's wiring
because a mail extension registers no blueprint. The sending app this record
needs moves it into an app, as the briefing recommends, and that change gets
its own record when it is built. Until a deployment cuts over, it sends as it
did.

## Alternatives considered

**A mailbox per deployment.** Isolates staging from production, at the cost of
two credentials, two inboxes and two provisioning runs per site. Not the
default, and still open to any site that wants it, as above.

**Google Workspace, which serves holdenweb.com's own mail.** A user mailbox for
the app costs a seat; a Group costs none but cannot be polled; reading a
mailbox needs a service account with domain-wide delegation. And every site's
mail would hang on one person's tenant.

**Opalstack mail on each deployment's own hostname.** MX, SPF and DKIM per
deployment, published by hand wherever the zone is served — for holdenweb.com,
DNSMadeEasy. Production's name already has Google's MX, so it would have needed
a subdomain anyway. Delegating one subdomain moves all of it into the
provider's own administrative space.

**A transactional mail service.** Sends and does not receive, and is an account
and a bill — the grounds on which holdenweb.com's HW-0030 rejected one.

**The `opalstack` PyPI library.** Read at 1.0.3, not run: its create call logs
the request at INFO, which for a mailbox includes the password; it logs every
POST body at DEBUG; and `wait_ready` polls every five seconds with no limit by
default. The provisioner needs three or four calls, which `urllib` makes
without a dependency or a logger to police.

**Provisioning from inside the application.** The token would live in the
container, and every deployment would hold the power to create mailboxes.
Creating one is an operator's act, like configuring the host.
