# Briefing: a contacts app, and a bulletin board

For sessions that will build two new podpack apps:

1. **Contacts** — public-facing enquiries, with the resulting **email threads**
   trackable from inside the app.
2. **A bulletin board** — either written as a podpack app, or an existing
   open-source forum integrated into the estate.

**This is a briefing, not a plan.** It records what the estate already is, what
it already forbids, and what it does not have. It proposes no schema, no
routes and no design. Those are for the sessions that follow, and for Steve to
approve before anything structural is built.

**Written 2026-09-29, revised to 2026-10-14**, against podpack `0.9.5+dev`,
holdenweb.com `1.8.5+dev`, staging running `6939fb5`. **Where mail lands (App
1) was rewritten on 2026-11-01**, once Steve had decided it and it had been
tested by hand, and §8 and open question 4 on 2026-11-02, when flask-paranoid
went. Version-sensitive claims carry their own date; treat this as a
document, not a test result, if you are reading it against something newer.

It lives in podpack because that is where app-authoring guidance lives, but it
spans the estate: links beginning `../holdenweb.com/` resolve only in a
`~/sites` layout holding both repositories, and will be dead for anyone who has
cloned podpack alone.

---

## How this was established, and how much to trust it

Most of it was verified by running something, on this machine, on 2026-09-29.
Where a claim was *not* driven, it says so.

Two warnings about method, because they invalidate the obvious way to check
this document:

**`grep` inside a Claude session is not `/usr/bin/grep`.** This is about the
tool, not the machine: Steve's own terminal has `/usr/bin/grep` and is
unaffected — `zsh -l -i -c 'type grep'` confirms it. But Claude Code's Bash
tool runs in a shell whose snapshot defines `grep` as a function routing to its
own bundled **ugrep** with `--ignore-files`, which honours `.gitignore`. It
shims `find` → **bfs** and `rg` the same way. Nothing in `~/.zshrc` does this;
the definitions live in `~/.claude/shell-snapshots/`.

The effect is not marginal. In `holdenweb.com`:

```
/usr/bin/grep -rl smtplib .   ->  15 files
grep -rl smtplib .            ->   5 files
```

The ten it drops include `scratch/mailtest.py` — a working standalone SMTP
client, and evidence that directly contradicts "nothing but flask-security
sends mail" — plus `.venv/`, `hostdata/`, `hostlogs/` and both `*.wpu`.

**So: `/usr/bin/grep`, or `grep --no-ignore-files`, before concluding anything
is absent.** Several absence claims in the research behind this document were
wrong for exactly this reason and were caught by an adversarial pass. Assume it
missed some.

**A search that finds nothing has two explanations.** Steve's standing rule,
and it earned its place here: the first sweep of this estate reported "no
contact mechanism on holdenweb.com" while `mailto:info@holdenweb.com` sits in
the footer of every page the site serves
([templates/base.html:81](../holdenweb.com/src/holdenweb/templates/base.html)).

---

## Read these first — they are better than this document

| | |
| --- | --- |
| [writing-an-app.md](writing-an-app.md) | 1,396 lines. The contract, a worked example, testing, shipping, and a `When it goes wrong` catalogue. **Read it end to end before writing a line.** |
| [README.md](README.md) | 1,558 lines. The reference: migrations, substrate, services, deployment. |
| [claude.md](claude.md) | Where the project is up to. §4 `Gotchas already paid for` is sixteen numbered traps; do not rediscover them. |
| [adrs/README.md](adrs/README.md) | 39 records, ADR-0001…0039. Records are immutable — supersede, never edit. |
| [holdenweb.com/adrs/README.md](../holdenweb.com/adrs/README.md) | 34 records, HW-0001…HW-0034. A bare `ADR-nnnn` always means podpack's. **The files are named `nnnn-slug.md` but titled `HW-nnnn` inside**, so grepping for a filename `HW-0009-…` finds nothing. |
| [holdenweb.com/ops/MESSAGES.md](../holdenweb.com/ops/MESSAGES.md) | 121 real error messages with what each means and what to do. Generated — edit `messages.json`, never the views. |

`tests/fixture_app/` is a complete canonical app in miniature, and
`podpack-notes` is the deliberate worked example: a model, a template, shipped
data, per-app config, an `init` and a nav entry.

---

## The estate, in twenty lines

Eight git repositories under `~/sites`, each published independently to PyPI by
trusted publishing on an `r*` tag.

| Repository | Version | What it is |
| --- | --- | --- |
| `podpack` | 0.9.5+dev | the framework |
| `podpack-pages` | 0.3.4 | ships **three** apps: `pages`, `pybooks`, `blog` |
| `podpack-notes` | 0.1.4+dev | the worked example |
| `podpack-notebooks` | 0.1.2+dev | static Jupyter notebooks |
| `podpack-pdf` | 0.2.2 | booklet maker — **the upload precedent** |
| `podpack-qrcode` | 0.2.2 | QR codes |
| `podpack-demo` | 0.2.0 | the site `creating-a-site.md` builds |
| `holdenweb.com` | 1.8.5+dev | the only deployed site |

One site runs in production: **staging**, at `https://os.holdenweb.com`, on
Opalstack host `opal17`. `holdenweb.com` the domain still points at unrelated
older technology. A site is one instance serving one site (ADR-0001) — there is
no multi-domain serving and no host-based routing.

---

## Nine things that are true, surprising, and will change your design

Each of these was verified by running it. Each one has bitten, or is waiting to.

### 1. The framework ships no mail. The *site* wires it.

podpack declares no mail library at all. `holdenweb.com` brings
`flask-mailman 1.1.1`, creates `Mail()` unbound at module level and binds it in
its own `_wire`, which podpack calls as `init` **before any app is installed**
([src/holdenweb/\_\_init\_\_.py:37,103](../holdenweb.com/src/holdenweb/__init__.py)).
ADR-0025 settles that this is the site's job, not the framework's.

The consequence for an app: `writing-an-app.md:703-710` is explicit — *"There is
no mechanism for 'this app requires mail', so check for it and degrade, or say
so in your README."* You can declare `needs_secrets`, `needs_tables`,
`defines_tables` and `backs_up`, and podpack refuses to boot a site that cannot
satisfy them (ADR-0034). You **cannot** declare that you need mail.

Reach it as `current_app.extensions["mailman"]`.

*Overtaken 2026-11-02: mail is an app, `podpack-mail`
([ADR-0041](adrs/0041-sending-mail-is-an-app.md)). The extension is still
`mailman`, so the line above stands; what changed is who installs it. An app
still cannot declare that it needs mail, but podpack now refuses to start a
site whose login sends mail with no transport installed.*

### 2. Sending is synchronous, inside the request, with two workers.

`smtp.gmail.com:465` over SSL as `steve@holdenweb.com`, `MAIL_PASSWORD` a Gmail
App Password declared in `config/app.toml`'s `secrets` list and delivered from
`secrets.env`. flask-mailman's SMTP backend re-raises `SMTPException`
(`fail_silently` defaults False), and flask-security catches nothing its mail
backend raises — which is why the site supplies `ReportingMailUtil`
([src/holdenweb/mail_util.py](../holdenweb.com/src/holdenweb/mail_util.py)), after
an expired credential turned "reset my password" into a 500.

`GUNICORN_WORKERS` defaults to 2. Two enquiries submitted while Gmail is slow
and the site has no worker left for anybody. There is no queue, no broker and
no retry anywhere in the estate.

### 3. There is no inbound path of any kind.

No IMAP or POP client, no SMTP listener, no Maildir or mbox reader, no mail
webhook, no broker, no worker, no Celery/RQ/APScheduler/Redis — in either
lockfile, in any repo. Nothing in the estate has ever read a message.

The only scheduler is **host cron on opal17** (`ops/backup.cron`, a checked-in
crontab), plus **UptimeRobot** polling `/healthz` from outside. Both are
recorded; neither is in a container.

`scripts/` and `ops/` are not in the image, so anything needing application
context must be a Flask CLI command run as
`podman compose exec -T web`. An in-process polling thread would multiply by
the worker count and fetch everything twice — which is precisely why the
one-shot `migrate` service exists.

### 4. You cannot capture a `Message-ID` after sending. Mint it yourself.

Decisive for threading. flask-mailman generates it *inside* `message()`, at
send time, only when the header is absent
(`flask_mailman/message.py:269-271`), from `socket.getfqdn()` — which inside
the container is podman's ephemeral hostname, not a domain the site owns. And
`send()` returns a **count**, not the message (`message.py:284-291`).

So: set `Message-ID` (and `In-Reply-To` / `References`) yourself in
`extra_headers`, under a domain you control, and store it in the same
transaction as the record it belongs to. Nothing in the estate does this today,
because flask-security is the only sender.

### 5. The mail DNS already authorises the deploy host to send as the site.

Confirmed on **2026-10-12** against the authoritative nameservers
(`ns10.dnsmadeeasy.com`), not just a local resolver:

| | MX | SPF | DMARC |
| --- | --- | --- | --- |
| `holdenweb.com` | Google (`aspmx.l.google.com` + alt1/alt2) | `v=spf1 include:_spf.google.com include:_spf.opalstack.com ~all` | `v=DMARC1; p=none; rua=mailto:steve@holdenweb.com;` |
| `os.holdenweb.com` | Opalstack (`mx1/mx2.de.opalstack.com`) | `v=spf1 include:spf.opalstack.com ~all` | `v=DMARC1; p=none` |

Three things follow, and the first is the useful one.

**`holdenweb.com`'s SPF already includes `_spf.opalstack.com`.** Mail sent from
the Opalstack host as `@holdenweb.com` passes SPF today — so the sending
identity an app would want is already authorised, without a DNS change. That is
a deliberate arrangement, not an accident of hosting.

**DMARC is `p=none` with aggregate reports to `steve@holdenweb.com`**, so
nothing is being rejected on alignment yet, and there is a feedback channel if
volume changes. There is no DKIM record at Google's default `google._domainkey`
selector, so alignment currently rests on SPF alone. (A selector can be named
anything; absence at the default one is not proof of no DKIM.)

**It is Google Workspace, not consumer Gmail** — confirmed by Steve,
2026-10-12. Note that **HW-0030** ("the site sends as a person")
says *consumer* throughout and reasons from it, including its rejection of SES,
Postmark and Mailgun on scale grounds. **That premise is wrong, so the costs
that record weighs are wrong.** Records are immutable; this wants a superseding
HW record, which did not exist when this was written and is now
[HW-0037](../holdenweb.com/adrs/0037-the-site-sends-as-itself-through-a-mailbox-of-its-own.md),
proposed 2026-11-01. Do not take HW-0030's mail economics at face value. (Not
to be confused with podpack's **ADR-0030**,
which is about apps reporting health — an unrelated record, and exactly why the
bare-number convention above matters.)

What Workspace makes available that consumer Gmail does not, and that bears
directly on App 1 — all of it subject to edition and admin settings, so confirm
in the console before designing on it:

- **Sub-addressing.** `enquiries+<token>@holdenweb.com` delivers to
  `enquiries@`, so a token in `Reply-To:` makes a reply self-identifying
  without matching `In-Reply-To`/`References` — which clients mangle or strip.
- **The Gmail API**, either polled from a CLI command on the existing cron
  line, or `users.watch` → Pub/Sub for genuine push. The second reintroduces a
  public POST endpoint on the serving host, which runs against what HW-0002,
  HW-0008 and HW-0009 record about ingestion there.
- **A service account with domain-wide delegation** scoped to one mailbox,
  instead of the long-lived Gmail App Password the site uses now.
- **`smtp-relay.gmail.com`**, which permits sending as any address in the
  domain. Today the envelope sender is forced to the authenticated user, which
  is why everything leaves as `steve@holdenweb.com`.
- **Groups as shared inboxes**, with an archive independent of any app.

And note that `os.holdenweb.com` has its own Opalstack MX — **the deployment's
hostname can already receive mail.** That is the cheapest candidate inbound
path in the estate and nobody has written it down. A candidate, not a decision.

*Overtaken 2026-11-01: `os.holdenweb.com` has no MX or TXT record today, and
the inbound path is decided — `mail.holdenweb.com`, delegated to Opalstack
(App 1).*

*Earlier draft of this document said both records were missing on
`holdenweb.com`. That was checked on 2026-09-29 and was true then as far as two
independent checks could tell; it is false now. Re-run the lookups rather than
trusting either reading.*

### 6. `SESSION_COOKIE_SAMESITE` is not set on the live site, though the code says it is.

Booted `holdenweb.create_app()` and read the config:

```
SESSION_COOKIE_SAMESITE    None        <- line 84 says 'strict'
SESSION_COOKIE_SECURE      False
REMEMBER_COOKIE_SAMESITE   'strict'    <- line 83 took
MAX_CONTENT_LENGTH         20971520
MAX_FORM_MEMORY_SIZE       500000
```

`_wire` is written entirely in `setdefault`, and **Flask ships
`SESSION_COOKIE_SAMESITE` in its own defaults with value `None`** — so
`setdefault` is a no-op. `REMEMBER_COOKIE_SAMESITE` is not a Flask key, so the
line above it works. Two adjacent lines, one effective and one dead.

Every Flask default is exposed to the same trap: `SESSION_COOKIE_SECURE`,
`MAX_CONTENT_LENGTH`, `MAX_FORM_MEMORY_SIZE`, `MAX_FORM_PARTS`,
`PREFERRED_URL_SCHEME`. **Check any config you set by reading it back off a
booted app, not by reading the source.**

`MAX_FORM_MEMORY_SIZE` is 500 kB and caps form *fields* independently of
`max_upload_bytes`. A long enquiry or a pasted forum post meets that limit
first.

### 7. There is no CSRF protection for a form you write yourself.

Booted app: `app.extensions` is `['podpack', 'sqlalchemy', 'mailman',
'security']`. No `csrf`. flask-security's own forms carry a token because they
are `FlaskForm`s; a hand-written `<form>` posting to your view has nothing.

And **an app cannot fix this**. flask-security requires `CSRFProtect` to be
initialised *before* it
(`flask_security/core.py:1902-1905`), and podpack's order is
`init(app)` → `auth.install` → `install_apps`. Your `SiteApp.init` runs after
flask-security, which is the order flask-security forbids. Site-wide CSRF is
installable only from the **site's** `_wire`.

Use `FlaskForm` for anything that takes a POST, or raise the question with
Steve.

### 8. Sessions are guarded by flask-login alone, which marks a moved session untrusted and evicts nobody.

*Rewritten 2026-11-02, when holdenweb.com dropped flask-paranoid (holdenweb.com
`1623a25` and `253aeab`). Until then this section described paranoid: an
unconditional site-wide `before_request` that, when `X-Forwarded-For[0]` or the
User-Agent changed, **cleared the session and the remember-me cookie and 302'd
to `/`**, with no message — so a visitor on mobile data or a VPN lost a
half-written form and was logged out, and no app could opt out of it.*

What guards a session now is flask-login's own protection, at `basic`. Read off
the site booted as a deployment on 2026-11-02: `SESSION_PROTECTION` is unset in
config, `login_manager.session_protection` is `'basic'`, and flask-principal's
is the only `before_request` hook. It computes
`sha512(X-Forwarded-For[0] | User-Agent)` (`_create_identifier`,
`flask_login/utils.py:392`, in flask-login 0.6.3), and two things about it
decide what a public form can do:

- **It does not evict.** `basic` sets `_fresh = False` and carries on
  (`login_manager.py:385-400`), so a session that moves is merely untrusted for
  `fresh_login_required` views rather than destroyed. `SESSION_PROTECTION =
  'strong'` would bring eviction back, on every page, if it were ever wanted.
- **It skips empty sessions** (`if sess and ident != sess.get("_id", None)`),
  so anonymous visitors are untouched — which is exactly the population filling
  in a public contact form.

So a long form, a multi-step flow or "come back and finish later" is no longer
wrong on this site. `X-Forwarded-For` is still read by two things — flask-login
and podpack's own centralised hop count — so the question of whom to trust for
it remains; removing paranoid took away a duplicate, not that.

### 9. There is exactly one role, and the nav shows everything to everybody.

`admin`, and nothing else — no moderator, editor, author or member exists in
code, config, migration or ADR. `Section` is a frozen `(label, endpoint)` pair
with no visibility predicate, and the context processor puts every entry in
front of every template. A "Start a topic" nav entry renders for anonymous
visitors; conditional chrome is the **site's**, not the app's.

Public sign-up **is** on, gated by email confirmation
([\_\_init\_\_.py:80-82](../holdenweb.com/src/holdenweb/__init__.py)) — so a
stranger can already get a confirmed account. A confirmed non-admin reaches
nothing an anonymous visitor does not, bar its own name in the nav.

---

## App 1 — contacts, and tracking email threads

Everything above applies. Three things are specific.

**The hard constraint: there is nowhere to put work that is not a request, and
the one place that exists cannot build a URL.**

Tracking a thread means correlating something that arrives later. Every
mechanism for that is missing (§3). The one supported shape is a Flask CLI
command on a host cron line — and on this site that command **cannot build an
absolute link to put in an email**. `holdenweb.com` sets no `[site] base_url`,
so `podpack.absolute_url()` falls through to `url_for(_external=True)`, which
raises outside a request context
(`podpack/src/podpack/urls.py:43-61`). `podpack-demo` sets `base_url`;
holdenweb.com deliberately does not, and `operations.md:761-765` records why.

**Settle that fork before drawing any schema.** It is one line of config and a
decision with recorded history.

**What protects a public POST today: nothing.** No rate limiting, no CAPTCHA,
no honeypot, no anti-spam of any kind, and no HTML sanitisation library
(`bleach`, `nh3`) anywhere. The estate's only sanitisation of a user-supplied
string is `secure_filename()` in podpack-pdf. Jinja autoescapes, which covers
display; it does not cover storing, mailing on, or rendering anywhere else.

**Where mail lands — decided by Steve, 2026-11-01**, and recorded in
[ADR-0040](adrs/0040-each-site-has-a-mailbox-and-each-deployment-an-address.md)
and
[HW-0037](../holdenweb.com/adrs/0037-the-site-sends-as-itself-through-a-mailbox-of-its-own.md),
both *proposed* until the sending app is built and cut over. They carry the
reasoning and the alternatives; this is what a design has to know.

**The app has a mailbox of its own, and it is not at Google.** Each site has
one Opalstack mailbox, `${SITE}-admin`, in `mail.holdenweb.com` — a subdomain
delegated to Opalstack, which publishes its MX, SPF, DKIM and DMARC itself.
Each deployment has an address delivering into it, `${SITE}-stag@` and
`${SITE}-prod@`; for this site, `hw-admin`, `hw-stag@` and `hw-prod@`. A
deployment logs in as the site's mailbox and sends from its own address.
Transactional mail moves there too, retiring the Gmail App Password of §2.

**The mailbox is shared, so a deployment must pick out its own mail.** Staging
and production read the same inbox. A fetch — IMAP, from a CLI command on host
cron (§3) — selects by `X-Original-To`, or staging acts on production's
correspondence.

**Verified by hand with `hw` on 2026-11-01**, sending with
`holdenweb.com/scratch/mailtest.py` (untracked):

- from `hw-stag@`, authenticated as `hw-admin` through
  `smtp.de.opalstack.com:465`, to a Workspace inbox: SPF, DKIM and DMARC all
  pass;
- a reply arrived with `X-Original-To` naming `hw-stag@`;
- `hw-stag+<tag>@` is delivered, so a token in `Reply-To:` can make a reply
  self-identifying without trusting `In-Reply-To`/`References`, which clients
  mangle or strip;
- **but `X-Original-To` drops the tag**, reading `hw-stag@`. The tag survives
  in `Delivered-To`, on the mailbox's own address
  (`hw-admin+tag@imap2.de.opalstack.com`), so the deployment comes from one
  header and the token from the other. A sender can put headers of either name
  into the message itself, and whether Opalstack strips them is not checked:
  set aside a message carrying more than one of either, and make the token
  unguessable, since anyone can send to a tagged address;
- the envelope sender arrives rewritten by SRS
  (`Return-Path: <SRS0=…@2.srs.de.opalstack.email>`), so a sender is read
  from `From:`, not `Return-Path`;
- the mailbox is on `imap2.de.opalstack.com`, a different machine from
  `imap.de.opalstack.com`; which name the fetch should connect to is not
  checked;
- a hyphenated mailbox name is accepted, and authenticates.

§4's `Message-ID` wants a domain the site controls; `mail.holdenweb.com` is now
the obvious one.

**`info@holdenweb.com` stays with people, and the app never reads it.** It is
advertised in the footer of every page
([templates/base.html:81](../holdenweb.com/src/holdenweb/templates/base.html)) and
is to be a **Google Group** whose members include whoever holds podpack's
`admin` role (Steve, 2026-10-12). That the Gmail API cannot poll a Group
constrained every design that had the app reading Google. It does not
constrain this one.

---

## App 2 — a bulletin board

**The decision is build-or-integrate, and the estate settles it harder than
the schema does.**

### If it is written as a podpack app

It is an ordinary app and the constraints are the ordinary ones — plus these,
which a board meets immediately and the estate has no precedent for:

- **No pagination exists anywhere.** `podpack-notes`' `page_size` is a bare
  `.limit()` with no offset, no count and no page links. A thread list is built
  from zero.
- **No app ships CSS or JS.** No app in the estate has a `static/` directory;
  no blueprint passes `static_folder`; the one stylesheet in any app is inlined
  into a `<style>` tag. `holdenweb.com/templates/base.html:8-11` states the
  arrangement outright: *the site styles the apps.*
- **And the styling block you would reach for does not exist on the real
  site.** podpack's default chrome defines `title`, `styles`, `content`;
  holdenweb.com's defines a dozen blocks but **no `styles`**. A block the
  chrome does not define is discarded silently (`writing-an-app.md:481-491`).
  So an app styled through `{% block styles %}` renders perfectly in a dev site
  and **arrives unstyled on the deployed one**. Nobody has decided whether a
  board ships its own assets or grows `holdenweb.com/static/styles.css`.
- **Moderation needs roles that do not exist** (§9).
- Prefix `__tablename__` with your app's name — `db.metadata` is flat and
  shared, and a clash breaks the *site*, not either app (rule 10).

### If an existing forum is integrated

Three independent walls converge, and none can be moved from inside an app.

**Schema.** One alembic history per site (ADR-0009), metadata assembled from
the config's app list, and `alembic/env.py` sets no `include_object`. A forum's
own tables sit in the same database declared by nothing — and the next
`alembic revision --autogenerate` faithfully writes `op.drop_table()` for every
one of them. The two exits are a separate database, or hand-maintained
revisions plus an `include_object` hook — and `alembic/env.py` is a
**substrate-managed** file, so editing it puts the site in three-way conflict
on every `podpack substrate upgrade` for as long as it lives.

**Services.** The core-services catalogue is closed to `postgres` and
`mongodb`, an app may not declare that it needs a backing service (ADR-0028),
and `podpack substrate services --add` rewrites the whole `COMPOSE_FILE` line
from that catalogue — silently deleting a hand-added overlay. Discourse wants
Redis and Sidekiq; NodeBB wants Redis or Mongo; Flarum wants PHP. None of that
arrives through the framework, and none of it is backed up: `backup.sh`
archives only what `podpack backup plan` reports from the closed catalogue.

**Address space.** The public edge is rented. HW-0031 is explicit: the site
owns no route, no vhost and no path prefix, and two deployments on one host
must be allocated different ports by hand from Opalstack's panel. No compose
file in the estate declares `networks`, `network_mode` or `extra_hosts`, so a
second stack cannot resolve `web` or `postgres` by name.

**So an integrated forum is a second Opalstack application on a second
hostname, not `/forum` on the existing site** — which means a second cookie
domain, and with flask-security and flask-paranoid both living on the first,
no shared login without SSO that does not exist. There is no OAuth, OIDC,
WebAuthn or magic link configured anywhere (the flask-security machinery for
some of it is installed and switched off).

The estate offers no middle path: **inherit identity, chrome, backup and deploy
by writing the board as an app, or run a separate product on a separate
hostname with a separate account system.**

---

## Traps that will cost you an afternoon

- **A git-sourced dependency will not build.** `git` is commented out of
  `holdenweb.com/Containerfile`'s builder stage, with the measurement that
  removed it (103.6 MB) and the condition for putting it back. `uv` shells out
  to a real `git` for any git-locked dependency, and the failure says *"Git
  executable not found"* — naming neither the file nor the dependency. **Publish
  to PyPI first**, or restore the layer deliberately.
- **Migrations are authored on the host, never in the container** (ADR-0011,
  gotcha 16), and there is one history per site. An app ships no migrations;
  every site regenerates your schema in its own history.
- **A table exists to alembic only if defining it is an import side effect of
  your `models.py`** (ADR-0010). A model in a module nothing imports is
  invisible to autogenerate.
- **`restart` is not a rebuild.** Source is baked into the image; use
  `./scripts/up.sh`, which always rebuilds and stamps the commit (gotcha 15).
  Only mounted config is a restart-only change.
- **Shipped `data/` seeds once, ever** (ADR-0008), and there is no once-only
  code hook — `SiteApp.init` runs every boot. App *upgrade* is deliberately
  deferred, and the trigger Steve named for solving it is precisely *an app
  that ships data expected to change with its code*.
- **You cannot declare a podpack version floor.** It remains prose and hope
  (`writing-an-app.md:1364+`).
- **`413` is uncatchable by an app**, and a `@blueprint.errorhandler(404)`
  covers only a 404 your own views raise — an unmatched URL under your prefix
  belongs to no blueprint and falls to the site.
- **Timezones have no convention.** One model in the estate does it right —
  `db.DateTime(timezone=True)` with `default=lambda: datetime.now(timezone.utc)`
  in podpack-notes. Copy it deliberately.
- **Don't name podpack in `[project.dependencies]`** — a dependency group
  instead. A real dependency's `[tool.uv.sources]` travels with the
  distribution and breaks any site that resolves it. This is measured, in
  `writing-an-app.md:98-208`.

---

## How to check any of this yourself

```bash
cd /Users/sholden/sites/holdenweb.com && SECRET_KEY=test-secret-key SECURITY_PASSWORD_SALT=test-salt MAIL_PASSWORD=test-mail-pw SQLALCHEMY_DATABASE_URI=sqlite:// PODPACK_ENVIRONMENT=local uv run python -c "import holdenweb; app = holdenweb.create_app(config_path='config/app.toml', data_root='/tmp/d', log_root='/tmp/l'); print(app.config['SESSION_COOKIE_SAMESITE'], list(app.extensions))"
```

That boots the real site with test secrets and prints its effective config —
the only reliable way to settle "is this setting actually set?".

And when searching for something you expect to be absent, defeat the tool's
gitignore-honouring shim:

```bash
/usr/bin/grep -r 'pattern' /Users/sholden/sites
```

---

## What I could not settle — for Steve, not for you to guess

1. **Whether `holdenweb.com` should set `base_url`.** It decides whether any
   scheduled work can put a link in an email (§App 1).
2. **Whether a board ships its own CSS** or grows the site's stylesheet — the
   first has no precedent and no working template block, the second makes the
   app undeployable anywhere else.
3. **Whether `SESSION_COOKIE_SAMESITE` and `SESSION_COOKIE_SECURE` should be
   set for real.** They are dead code today (§6). This is a live defect,
   independent of either new app.
4. ~~**Whether flask-paranoid stays.**~~ *Settled 2026-11-02: it went*
   (holdenweb.com `1623a25` and `253aeab`), on the analysis of 2026-10-14:
   flask-login's `basic` protection already runs on the same signal without
   evicting, so what went was hard eviction of a moved session (§8). Of what
   was to compensate, the cookie flags are now really set — read off a booted
   deployment the same day, the session cookie is `SameSite=Lax` and `Secure`
   (HW-0035) — but `PERMANENT_SESSION_LIFETIME` is still 31 days. Those protect
   the cookie from being captured, which an IP check never did.
5. **Whether mail handling belongs in podpack rather than in each site.**
   Raised by Steve, 2026-10-12. The recommendation below was reversed on
   2026-10-14 after both premises behind it were falsified: both halves are now
   recommended as apps, with one condition.
6. **Whether podpack gains a `requires_apps` declaration**, so an app can
   state that another app must be *enabled*. See below.

### Recommended: both halves are apps, and optionality must fail loudly

A recommendation awaiting Steve's decision, not a settled design.

**Sending and receiving are both apps.** One distribution can ship the pair
(ADR-0038), which keeps the `Message-ID` a send mints in the same codebase as
the receive that matches on it. A site that sends no mail installs nothing.

**This reverses the recommendation recorded here on 2026-10-12**, which was
sending built into the framework as a service. Both premises it rested on were
checked on 2026-10-14 and both were wrong — worth stating, because they are the
objections anyone will raise again:

- *"flask-security needs its `MailUtil` before apps install, so an app cannot
  serve core's password-reset mail."* False.
  `flask_security/mail_util.py:51-56` — `MailUtil.__init__` is `pass`, and
  `send_mail` resolves the transport per-send with
  `current_app.extensions.get("mailman")`, inside a request. Only the *class*
  need exist at `auth.install`, and a class is an import. The install order
  (`init(app)` → `auth.install` → `install_apps`,
  [src/podpack/__init__.py](src/podpack/__init__.py):165-172) does not bite.
- *"Sending is a service in ADR-0028's sense."* A category error.
  `src/podpack/services/__init__.py:1` defines a service as *"the backing
  stores a site can choose to run"* — a compose overlay with a container. Mail
  is an outbound client with nothing to run, so routing it there would have
  meant inventing a second meaning of "service".

What app-shaped sending buys:

- **It fixes a defect already on the record.** HW-0030 says the mail settings
  can be overridden from the environment. They cannot — tested 2026-10-14,
  `MAIL_SERVER` and `MAIL_PORT` set in the environment left `app.config` at
  `smtp.gmail.com:465`, because `_wire` reads `os.environ` for `MAIL_PASSWORD`
  alone. As `[apps.mail]` config it becomes reviewable and genuinely
  overridable.
- **`needs_secrets={"MAIL_PASSWORD"}`** gives the boot-time check per app
  rather than per site.
- **`status()` and `healthz()`** (ADR-0030) give `/_status` a mail report and a
  non-fatal health line. That is why `flask --app holdenweb mail check` exists
  as a bespoke command today: there was nowhere idiomatic to put it.
- **One implementation** instead of ~20 lines of `_wire` in every site — the
  ADR-0033 argument, one layer down.

The two real costs:

- **An app with no routes bends the contract.** ADR-0025's reason mail was not
  an app is that it "registers no blueprint", and a sending app still registers
  none; it would carry a blueprint only to own its name (ADR-0003). Legal, but
  odd.
- **Silent degradation, and it is worse than today.** `send_mail`'s lookup is
  `if current_app.extensions.get("mailman", None):` with **no else branch**. A
  site with `SECURITY_CONFIRMABLE` on and no mail app enabled would accept
  registrations and send nothing at all — no error, no log. Today
  `MAIL_PASSWORD` in `secrets` makes that a boot failure. **So optionality
  requires a boot-time check that a site whose config implies mail has it.**
  Note that check is neither `needs_secrets` nor `requires_apps`: it is
  *site-config → app*, a third relation, and it is the part to decide
  deliberately rather than discover.
  *Corrected 2026-11-02: flask-security 5.8.2 and 5.9.2 do have an else
  branch — `raise ValueError("No email extension configured")` — so the
  failure is loud, at first use, in front of a visitor. The boot check was
  built anyway, to fail before that: `podpack.auth.check_mail`
  ([ADR-0041](adrs/0041-sending-mail-is-an-app.md)).*

Unchanged from the earlier recommendation: deployment settings follow the
deployment (ADR-0018/0036/0039, including 0039's lesson that a variable shipped
in a *seeded* file never reaches an existing site); failure policy stays the
site's through `PODPACK_MAIL_UTIL_CLS`, which podpack reads as a class object at
`auth.install` and an app therefore cannot set — the app ships the class, the
site names it; and transactional mail stays apart from correspondence.
*Overtaken 2026-11-01 on that last point: HW-0037 moves holdenweb.com's
transactional mail into the site's own mailbox (App 1).*

### Settled: podpack does not do package management

Steve, 2026-10-12, and recorded so it is not reproposed. The objection that
`requires_apps` carries no version is **answered by the layer below it**: uv and
PyPI already enforce versioned dependencies, which is what a package manager is
for. An app that requires another app expresses the *version* constraint as an
ordinary dependency and the *enablement* constraint through podpack — and those
are genuinely different facts, since ADR-0004's whole point is that installed
is not enabled.

So `requires_apps` would assert enablement only: **refuse at boot, never
resolve, never install.** podpack is to stay free of version floors, transitive
resolution and ordering solvers in the name of simplicity. Steve also notes the
estate is simple enough to be a monorepo, and arguably should be, which would
change this calculus — it is not one today.

**Nothing here is to be built yet.** His words: *leave things as they are for
now.*

---

*Prepared by Claude, 2026-09-29 and revised through 2026-10-14, from a 17-agent
survey of the estate with an
adversarial pass over every absence claim, and re-verified by hand where it
mattered. The adversarial pass corrected roughly a dozen findings; assume it
missed some.*
