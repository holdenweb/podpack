"""Absolute URLs for a site, for the times a request cannot supply one.

Inside a request Flask already knows the site's address: `url_for(...,
_external=True)` builds it from the incoming `Host` header and needs no
configuration at all. Outside one it raises, and that is the gap this fills --
feeds, mail sent from a job, a CLI command, anything generating a link with no
browser on the other end.

Deliberately *not* implemented by setting Flask's `SERVER_NAME`, which would
also make `_external=True` work everywhere. Two reasons, in order of how much
they bite:

- Every request whose `Host` differs from it logs `UserWarning: Current server
  name '...' doesn't match configured server name '...'`. The container
  healthcheck hits `127.0.0.1:8000` every ten seconds, so a site with a public
  `SERVER_NAME` would warn on each one, for ever.
- With `subdomain_matching=True` a mismatched `Host` becomes a 404 -- measured,
  and the healthcheck is exactly such a request. That is opt-in rather than the
  default in Flask 3.1, but it is a loaded gun left in the drawer.

A site's canonical address is data about the site. Treating it as data, rather
than as routing configuration, keeps it from having opinions about which
requests are allowed to arrive.
"""

from urllib.parse import urlsplit

from flask import current_app, url_for


def base_url():
    """The site's canonical public address, or None if it has not set one.

    Note this is the address the *world* uses, which on a proxied host is
    nothing like where the application listens: Opalstack serves
    `https://example.com/` and proxies to a container on some allocated high
    port. The two coincide only in a lab.
    """
    return current_app.extensions["podpack"].host_config.get("site", {}).get("base_url")


def absolute_url(endpoint, **values):
    """Build a fully-qualified URL for `endpoint`.

    Where a site has set `base_url`, this binds the URL map to that address
    directly rather than going through `url_for`. It has to: outside a request
    `url_for` raises before it builds anything, even for a relative path, so
    joining its result to a prefix is not an option.

    Where a site has not, it falls back to `_external=True`, which works inside
    a request and raises a clear enough error outside one.
    """
    root = base_url()
    if not root:
        return url_for(endpoint, _external=True, **values)
    parts = urlsplit(root)
    adapter = current_app.url_map.bind(
        parts.netloc, script_name=parts.path or "/", url_scheme=parts.scheme
    )
    return adapter.build(endpoint, values, force_external=True)


def check_base_url(host_config):
    """Refuse to boot on a `base_url` that cannot be joined to.

    `urljoin` silently does the wrong thing with a relative or scheme-less
    value -- `urljoin("example.com", "/x")` is `/x`, an address no mail client
    can follow -- so the useless cases are worth catching at boot rather than in
    whatever link is sent first.
    """
    value = host_config.get("site", {}).get("base_url")
    if value is None:
        return
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        raise RuntimeError(
            f"[site] base_url is {value!r}, which has no scheme and host. It is "
            "the site's canonical public address, so it needs to look like "
            "'https://example.com'."
        )
