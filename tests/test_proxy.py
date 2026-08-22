"""Believing the proxy about the scheme, and only when told to.

Written after a real reset mail from a real deployment arrived carrying
`http://os.holdenweb.com/reset/<token>`. The host in that link was already
right -- nginx passes `Host` through -- so the only thing missing was the
scheme, and the only thing missing to get it was permission to read
`X-Forwarded-Proto`.

The test that matters most here is not that the scheme becomes `https`; it is
`test_the_host_follows_the_request`, which pins the property that made this
approach worth choosing over configuring the site's own address. On a managed
host the domain binding belongs to the control panel, so a site that had to be
told its name would go wrong the moment somebody changed it there.

Every request below goes through the test *client*, and that is not
incidental. `ProxyFix` is WSGI middleware, so it runs only when a request
passes through `app.wsgi_app`; `test_request_context` builds the environ
directly and never reaches it. The first draft of these tests used the context
and reported that the middleware did nothing, which was true only of the
instrument.
"""

import pytest
from flask import Flask, url_for

from podpack.proxy import PROXY_HOPS, proxy_hops

from conftest import SiteFactory

# Incidental -- every external URL is built the same way -- but a route
# podpack always has keeps this from depending on the fixture app.
ENDPOINT = "podpack.status"

HTTPS = {"X-Forwarded-Proto": "https"}


def mailing_site(site: SiteFactory, **overrides: object) -> Flask:
    """A site with one extra route: the URL it would put in an outgoing mail."""
    app = site(**overrides)
    app.add_url_rule("/_probe", "_probe", lambda: url_for(ENDPOINT, _external=True))
    return app


def link(app: Flask, host: str = "os.example.com", **headers: str) -> str:
    """What that route answers for a request arriving as described."""
    response = app.test_client().get(
        "/_probe", base_url=f"http://{host}", headers=headers
    )
    return response.get_data(as_text=True)


def test_an_unproxied_site_does_not_believe_the_header(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default, and the behaviour that produced the bug.

    Sending the header is not enough, and must not be: a site with nothing in
    front of it is reachable by whoever forged it.
    """
    monkeypatch.delenv(PROXY_HOPS, raising=False)

    assert link(mailing_site(site), **HTTPS).startswith("http://")


def test_a_declared_proxy_makes_the_scheme_the_visitors(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROXY_HOPS, "1")

    assert link(mailing_site(site), **HTTPS) == "https://os.example.com/_status"


def test_the_host_follows_the_request(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebinding the domain must not need a redeploy, or an edit anywhere.

    This is why the fix reads the request rather than configuring the site's
    address: one running site answers correctly under whatever name reaches
    it, including a name nobody knew about when it was deployed.
    """
    monkeypatch.setenv(PROXY_HOPS, "1")
    app = mailing_site(site)

    assert link(app, "os.example.com", **HTTPS).startswith("https://os.example.com/")
    assert link(app, "renamed.example", **HTTPS).startswith("https://renamed.example/")


def test_a_declared_proxy_that_sends_no_header_changes_nothing(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declaring the hop grants permission; it does not invent a scheme."""
    monkeypatch.setenv(PROXY_HOPS, "1")

    assert link(mailing_site(site)).startswith("http://")


def test_the_forwarded_host_is_believed_too(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy that rewrites `Host` and reports the original is believed.

    nginx on the deployment this was written for does not do that, so the
    header is absent there and `Host` passes through untouched. It is covered
    because trusting the chain for the scheme but not for the host would be a
    distinction with nothing behind it.
    """
    monkeypatch.setenv(PROXY_HOPS, "1")

    url = link(
        mailing_site(site),
        "container.internal",
        **HTTPS,
        **{"X-Forwarded-Host": "public.example.com"},
    )

    assert url.startswith("https://public.example.com/")


def test_status_reports_what_the_proxy_said_and_what_was_concluded(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So the question is answerable on a running host without mailing anybody."""
    monkeypatch.setenv(PROXY_HOPS, "1")

    reported = (
        site()
        .test_client()
        .get("/_status", base_url="http://os.example.com", headers=HTTPS)
        .get_json()["proxy"]
    )

    assert reported == {
        "hops_trusted": 1,
        "forwarded_proto": "https",
        "scheme": "https",
        "host": "os.example.com",
    }


def test_status_says_when_no_header_arrived(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`(not sent)` and `http` separate the two ways of getting this wrong."""
    monkeypatch.delenv(PROXY_HOPS, raising=False)

    reported = site().test_client().get("/_status").get_json()["proxy"]

    assert reported == {
        "hops_trusted": 0,
        "forwarded_proto": "(not sent)",
        "scheme": "http",
        "host": "localhost",
    }


@pytest.mark.parametrize("value", ["banana", "1.5", "one"])
def test_an_unreadable_hop_count_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(PROXY_HOPS, value)

    with pytest.raises(RuntimeError, match=PROXY_HOPS):
        proxy_hops()


def test_a_negative_hop_count_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROXY_HOPS, "-1")

    with pytest.raises(RuntimeError, match="cannot be"):
        proxy_hops()


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_setting_is_the_same_as_none(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A variable left empty in `.env` is somebody not setting it."""
    monkeypatch.setenv(PROXY_HOPS, value)

    assert proxy_hops() == 0
