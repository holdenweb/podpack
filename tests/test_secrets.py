"""The boot-time secrets check.

`SECURITY_PASSWORD_SALT` became required and a site started anyway, serving
every route, and failed hours later inside `flask users create`. `require_env`
had been checking secrets one at a time for as long as there had been secrets,
which on a containerised deployment costs a rebuild per missing name.
"""

import pytest

from podpack.config import FRAMEWORK_SECRETS, check_secrets, framework_secrets

# The second element of each mapping entry is a *label* -- who says the
# secret is needed -- and appears in the failure message. Named so that it
# cannot be misread as a value.
NEEDED_BY_THE_SITE = "[site] secrets"

GOOD = {
    "SECRET_KEY": "a-real-looking-value",
    "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u:p@h/db",
    "SECURITY_PASSWORD_SALT": "another-real-looking-value",
}


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in GOOD.items():
        monkeypatch.setenv(name, value)


def test_a_fully_configured_site_passes(configured: None) -> None:
    check_secrets(framework_secrets({}))


def test_every_missing_secret_is_named_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point. One name per restart is one rebuild per name."""
    for name in FRAMEWORK_SECRETS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError) as caught:
        check_secrets(framework_secrets({}))

    for name in FRAMEWORK_SECRETS:
        assert name in str(caught.value)


def test_an_empty_value_counts_as_missing(configured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """`SECRET_KEY=` in an env file is not a configured site, whatever the
    shell thinks it is."""
    monkeypatch.setenv("SECRET_KEY", "   ")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        check_secrets(framework_secrets({}))


def test_a_site_declares_its_own(configured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """podpack cannot know that a site sends mail; the site can say so.

    Without this the failure surfaces when a visitor asks for a password
    reset, which is both later and somebody else's problem.
    """
    monkeypatch.delenv("MAIL_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="MAIL_PASSWORD"):
        check_secrets({"MAIL_PASSWORD": NEEDED_BY_THE_SITE})


def test_a_declared_secret_that_is_set_passes(configured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_PASSWORD", "sixteen-chars-ok")
    check_secrets({"MAIL_PASSWORD": NEEDED_BY_THE_SITE})


@pytest.mark.parametrize(
    "value, why",
    [
        ("CHANGEME", "what substrate writes for a value it cannot know"),
        ("postgresql+psycopg2://u:CHANGEME@h/db", "embedded in a longer value"),
        ("@@DB_PASSWORD@@", "a rendering token that was never substituted"),
    ],
)
def test_an_unedited_placeholder_is_refused(
    configured: None, monkeypatch: pytest.MonkeyPatch, value: str, why: str
) -> None:
    """A file installed and never edited, which is not a value but the absence
    of one wearing a value's clothes.

    It matters because these *work*: a site boots on CHANGEME, serves every
    page, and is wrong in a way nothing reports until something needs the
    secret to be real.
    """
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", value)
    with pytest.raises(RuntimeError, match="placeholder"):
        check_secrets(framework_secrets({}))


def test_the_message_says_where_secrets_come_from(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader of this message is deploying, and probably tired."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="secrets.env"):
        check_secrets(framework_secrets({}))


NEEDY_APP = '''
from flask import Blueprint
from podpack import SiteApp

blueprint = Blueprint("needy", __name__)


@blueprint.route("/")
def index() -> str:
    return "hi"


site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/needy",
    needs_secrets=frozenset({"MAPS_API_KEY"}),
)
'''


def test_an_app_declares_what_it_cannot_run_without(site, app_package, monkeypatch):
    """The author knows; the site owner installing the app has no way to.

    Without this, installing an app that needs a credential produces a site
    that starts, serves, and fails the first time somebody uses the feature.
    """
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    app_package("needy_app", NEEDY_APP)

    with pytest.raises(RuntimeError) as caught:
        site(host_config={"site": {"name": "x", "environment": "test", "apps": ["needy_app"]}})

    message = str(caught.value)
    assert "MAPS_API_KEY" in message
    # Naming the app matters: it is what tells you whether to supply the
    # secret or to stop installing the thing that wants it.
    assert "needy" in message


def test_an_app_whose_secret_is_present_installs(site, app_package, monkeypatch):
    monkeypatch.setenv("MAPS_API_KEY", "a-real-looking-key")
    app_package("needy_app_ok", NEEDY_APP.replace('"needy"', '"needyok"'))

    app = site(host_config={"site": {"name": "x", "environment": "test", "apps": ["needy_app_ok"]}})
    assert "needyok" in app.extensions["podpack"].apps
