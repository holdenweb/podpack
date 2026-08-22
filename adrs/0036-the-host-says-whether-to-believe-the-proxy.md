# ADR-0036: The host says whether to believe the proxy

**Status:** Accepted

**Date:** 2026-08-21

## Context

A podpack site does not listen to the world. It binds loopback —
`WEB_BIND_ADDR=127.0.0.1` in the shipped `env.example`, and the compose file
defaults to it — and something in front proxies to it. On a managed host that
is nginx; in a lab it is whatever the lab puts there. So the request Flask
sees is not the request the visitor made, and until now nothing reconciled the
two.

The scheme is the part that bites. A visitor arrives over TLS, the proxy
forwards plain HTTP, and every URL built with `_external=True` comes out
`http://`. Nothing logs it, because from the application's side nothing is
wrong.

This was found the way such things are found. A password-reset mail from
`os.holdenweb.com` — a live deployment — arrived carrying
`http://os.holdenweb.com/reset/<token>`. nginx answers that with a 301 to
`https` and sends HSTS, so the link *works*, and had worked for as long as the
site had been up. But HSTS protects only a browser that has been to the site
before, so a recipient opening the mail on a device that has not sends the
reset token in the path of a cleartext request before any redirect happens.

Note what was already right in that link: **the host**. The reset URL is built
inside the request that asked for it, and nginx passes the visitor's `Host`
through untouched. Only the scheme was missing.

Three things were considered and are recorded because each looks plausible:

- **`SERVER_NAME` with `PREFERRED_URL_SCHEME`.** Rejected already, in
  `urls.py`, for reasons that still hold: every request whose `Host` differs
  logs a warning, and the container healthcheck makes such a request every ten
  seconds for ever. `PREFERRED_URL_SCHEME` alone does nothing here — measured —
  because inside a request context Flask builds from the environ.
- **`base_url`, the setting podpack already has.** It would not have helped.
  It feeds exactly one function, `podpack.absolute_url`, and Flask-Security
  builds its reset link with plain `url_for(_external=True)`. The site's own
  backlog recorded `base_url` as the fix for this for two days; it was wrong.
- **gunicorn's `forwarded_allow_ips`.** gunicorn will rewrite the scheme
  itself, but only for a peer address in that list, which defaults to
  `127.0.0.1`. Under rootless podman the address it sees is the port
  forwarder's, so the default silently declines. Correct to set, but it makes
  the site's behaviour depend on which podman port driver is in use.

## Decision

`ProxyFix` in the application, applied when — and only when — the **host**
says how many proxies stand in front of it:

```
PODPACK_PROXY_HOPS=1        # in .env, on the host where it is true
```

Unset means zero, which means no middleware at all and the request read
exactly as it arrives.

**The declaration is an environment variable, not a key in `config/app.toml`.**
This is the substance of the decision rather than a detail of it. Trusting
`X-Forwarded-*` is a claim about a deployment — how many proxies are in front,
and whether anything else can reach the port — and not about a site.
`config/app.toml` is committed and bind-mounted read-only, and the *same file*
runs on a laptop with no proxy and on a managed host with one, so any value
there is wrong in one of the two places. `.env` already carries facts of
exactly this kind, `WEB_BIND_ADDR` and `WEB_HOST_PORT` among them.

All four hop counts move together: `x_for`, `x_proto`, `x_host`, `x_port`.
They are not four decisions. The question is whether this chain is trusted,
and a deployment trusting it for the scheme but not for the host is describing
a situation nobody has. `ProxyFix` overrides only where the header is present,
so naming one the proxy does not send costs nothing.

`/_status` reports both halves — what arrived in `X-Forwarded-Proto`, and what
the site concluded — because either alone is ambiguous. A site reporting
`http` may be behind a proxy sending no header, or in front of one it has not
been told to trust, and the remedy differs. It is also the only way to settle
the question on a running host without mailing somebody a reset link and
reading it.

## Consequences

**A site is never told its own address, and that is the point.** The domain a
managed host binds belongs to its control panel and can change without the
site being redeployed. Because the URL is built from the request, one running
site answers correctly under whatever name reaches it — including a name
nobody knew about when it was deployed. `tests/test_proxy.py` pins this as
`test_the_host_follows_the_request`, and it is the test that argues for this
approach over configuring an address.

**`base_url` keeps a narrower job.** It is for the genuinely request-less
case — a cron job, a CLI command building a link with no browser on the other
end. A site whose links are all built inside requests needs no such setting,
and `holdenweb.com` is one: every `url_for` in it is inside a view.

**Existing sites are unchanged until their host says otherwise.** Zero is the
default and no middleware is installed, so nothing about an unproxied site
moves. `env.example` documents the variable, but ADR-0026 means a site with a
seeded `.env` will not receive it — so it is in `operations.md` as a step, not
only in a template.

**A deployment that widens `WEB_BIND_ADDR` must revisit this.** Loopback is
what makes one hop safe: the headers can only come from a proxy on the same
host. A site published on all interfaces and trusting a hop is trusting
whoever reaches the port.
