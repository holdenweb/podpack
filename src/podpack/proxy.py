"""Whether to believe the proxy in front of this site, and how far.

A podpack site does not listen to the world. It binds loopback -- `.env` ships
`WEB_BIND_ADDR=127.0.0.1` and the compose file defaults to it -- and something
in front proxies to it: nginx on a managed host, whatever a lab puts there. So
the request Flask sees is not the request the visitor made.

The scheme is the part that bites. The visitor arrives over TLS, the proxy
forwards plain HTTP, and every URL built with `_external=True` comes out
`http://`. Nothing logs it, because nothing is wrong as far as the application
can see; the first thing to carry such a URL is usually a password-reset mail,
so the symptom surfaces in somebody's inbox. Measured on this site's own
deployment before this module existed: a reset mail from `os.holdenweb.com`
carried an `http://os.holdenweb.com/reset/...` link, one redirect and one
cleartext request away from the token it was protecting.

Note what was *already* right in that link: the host. The reset URL is built
inside the request that asked for it, and nginx passes the visitor's `Host`
through, so a site never has to be told which domain it answers on -- which
matters, because on a managed host that binding belongs to the control panel
and can change without the site being redeployed. Only the scheme was missing.
`podpack.urls.base_url` remains the answer for the genuinely request-less case
-- a cron job, a CLI command building a link with no browser on the other end
-- and a site whose links are all built inside requests needs no such setting
at all.

## Why an environment variable, and not `[site]` in the config file

`X-Forwarded-*` headers are trivially forgeable by anything that can reach the
application port, so trusting them is a claim about the deployment:

- how many proxies stand between the internet and gunicorn, and
- whether anything else can reach that port at all.

Neither is a property of the site. `config/app.toml` is committed and
bind-mounted read-only, and the same file runs on a laptop with no proxy in
front and on a managed host with one -- so any value there is wrong in one of
the two places. `.env` on the host already carries facts of exactly this kind,
`WEB_BIND_ADDR` and `WEB_HOST_PORT` among them, and this belongs beside them.

Unset means zero, which means no middleware at all and the request read exactly
as it arrives. That is the right default rather than a cautious one: a site
with nothing in front of it must not believe a forwarded header, and a site
with something in front says so once, on the host where it is true.

## Why the count is usually derived, and not set

`PODPACK_PROXY_HOPS` was a value every deployment had to set and keep correct,
and it is the one variable podpack cannot deliver: its whole documentation
lives in `env.example`, a *seeded* substrate file that `podpack substrate
upgrade` never rewrites (ADR-0026). So an existing site is never told the
variable exists, and the failure when it is forgotten is silent -- every page
serves and only absolute URLs are wrong.

But the value was never really free. It is a function of how the site is
deployed, and `PODPACK_ENVIRONMENT` already records that: `local` for a lab,
anything else for a real deployment behind a real proxy. So the count is
derived from it -- zero when local, one otherwise -- and a derived value
cannot be forgotten, cannot go undelivered, and cannot drift from the
deployment it describes.

`PODPACK_PROXY_HOPS` remains, and still wins where it is set. Two deployments
need it: one behind two proxies, and a non-local one behind none, which must
now say `PODPACK_PROXY_HOPS=0` rather than leaving it unset. Both are unusual
enough to be worth an explicit line; the common case is no line at all.

**An unrecognised environment counts as not-local**, matching
`prepare-host-dirs.sh`, which made the same choice for the same reason: a typo
in that variable should not quietly turn a real deployment into a lab.

## Why gunicorn's own setting is not enough

gunicorn will do this itself, but only for a peer address in
`forwarded_allow_ips`, which defaults to `127.0.0.1`. Under rootless podman the
address it sees is the port forwarder's, not loopback, so the default silently
declines and the header is dropped before Flask is reached. Doing it in the
application removes the dependency on which port driver happens to be in use.
"""

import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

PROXY_HOPS = "PODPACK_PROXY_HOPS"
"""Names the override variable, so the checks and the messages agree."""

ENVIRONMENT = "PODPACK_ENVIRONMENT"
"""The variable the count is derived from when the override is absent."""

LOCAL = "local"
"""The one value meaning "nothing is in front of this"; unset means this too."""

HOPS_WHEN_DEPLOYED = 1
"""What a non-local deployment gets: one nginx between it and the internet.

A deployment with a second proxy says so with `PODPACK_PROXY_HOPS`, which is
the whole reason the override survives. One is not a guess at the general
case; it is the only topology this substrate deploys into, and the substrate
is what puts a site behind a proxy in the first place.
"""


def deployment_environment() -> str:
    """Which kind of deployment this is. `local` when nothing says otherwise.

    Unset means `local` for the same reason `prepare-host-dirs.sh` says so:
    every site created before the variable existed is effectively a lab, and
    breaking them to make a point would be the wrong trade.
    """
    return os.environ.get(ENVIRONMENT, "").strip() or LOCAL


def declared_hops() -> int | None:
    """What `PODPACK_PROXY_HOPS` says, or `None` where it says nothing.

    Refuses a value it cannot read rather than falling back: an unparseable
    count is a deployment somebody meant to proxy, and silently serving it
    unproxied reproduces exactly the bug this module exists to fix. Note that
    an explicit `0` is *not* nothing -- it is how a non-local deployment with
    no proxy declines the hop it would otherwise be given.
    """
    raw = os.environ.get(PROXY_HOPS, "").strip()
    if not raw:
        return None
    try:
        hops = int(raw)
    except ValueError:
        raise RuntimeError(
            f"{PROXY_HOPS} is {raw!r}, which is not a whole number. It counts "
            "the proxies between the internet and this site: 1 where a single "
            "nginx forwards to it, 0 where nothing does. Unset it to let "
            f"{ENVIRONMENT} decide."
        ) from None
    if hops < 0:
        raise RuntimeError(
            f"{PROXY_HOPS} is {hops}, and a count of proxies cannot be "
            "negative. Set it to 0 to trust none."
        )
    return hops


def proxy_hops() -> int:
    """How many proxies to believe: what was declared, else what is deployed."""
    declared = declared_hops()
    if declared is not None:
        return declared
    return 0 if deployment_environment() == LOCAL else HOPS_WHEN_DEPLOYED


def hops_source() -> str:
    """Which variable decided the count, for `/_status` to report.

    The number alone does not say whether somebody chose it or it followed
    from the environment, and those are fixed in different files.
    """
    if declared_hops() is not None:
        return PROXY_HOPS
    return ENVIRONMENT


def trust_proxy(app: Flask, hops: int) -> None:
    """Read the visitor's scheme, host and address out of forwarded headers.

    All four hop counts move together on purpose. They are not four decisions:
    the question is whether this chain of proxies is trusted, and a deployment
    that trusts it for the scheme but not for the host is describing a
    situation nobody has. `ProxyFix` overrides only where the corresponding
    header is actually present, so naming a header the proxy does not send
    costs nothing.
    """
    if not hops:
        return
    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops
    )
