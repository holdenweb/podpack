"""The boot-time check that a site whose login sends mail can send it (ADR-0041).

flask-security looks its transport up per send and, with none, raises on the
first registration or password reset -- in front of a visitor. Each feature
below was read off one of flask-security's own `send_mail` call sites. Each is
switched on alone and must stop the site, and none may stop it once a transport
is installed.
"""

import re
from typing import Any

import pytest
from flask import Flask

from conftest import SiteFactory
from podpack import auth

# One configuration per row of auth.MAILING_FEATURES, written as a site would.
MAILING: dict[str, dict[str, Any]] = {
    "CONFIRMABLE": {"SECURITY_CONFIRMABLE": True},
    "REGISTERABLE with SEND_REGISTER_EMAIL": {"SECURITY_REGISTERABLE": True},
    "RECOVERABLE": {"SECURITY_RECOVERABLE": True},
    "CHANGEABLE with SEND_PASSWORD_CHANGE_EMAIL": {"SECURITY_CHANGEABLE": True},
    "CHANGE_EMAIL": {"SECURITY_CHANGE_EMAIL": True},
    "CHANGE_USERNAME with SEND_USERNAME_CHANGE_EMAIL": {"SECURITY_CHANGE_USERNAME": True},
    "USERNAME_RECOVERY": {"SECURITY_USERNAME_RECOVERY": True},
    "PASSWORDLESS": {"SECURITY_PASSWORDLESS": True},
    "TWO_FACTOR, by email or with its rescue mail": {
        "SECURITY_TWO_FACTOR": True,
        "SECURITY_TWO_FACTOR_ENABLED_METHODS": ["authenticator"],
        "SECURITY_TWO_FACTOR_RESCUE_EMAIL": True,
    },
    "UNIFIED_SIGNIN by email": {"SECURITY_UNIFIED_SIGNIN": True},
}

# The same features with their own SEND_* switch off: nothing goes out, so
# nothing is needed.
SILENT: dict[str, dict[str, Any]] = {
    "registration without its email": {
        "SECURITY_REGISTERABLE": True,
        "SECURITY_SEND_REGISTER_EMAIL": False,
    },
    "recovery with both of its emails off": {
        "SECURITY_RECOVERABLE": True,
        "SECURITY_SEND_PASSWORD_RESET_EMAIL": False,
        "SECURITY_SEND_PASSWORD_RESET_NOTICE_EMAIL": False,
    },
    "a password change without its notice": {
        "SECURITY_CHANGEABLE": True,
        "SECURITY_SEND_PASSWORD_CHANGE_EMAIL": False,
    },
    "two-factor by app alone, with no rescue mail": {
        "SECURITY_TWO_FACTOR": True,
        "SECURITY_TWO_FACTOR_ENABLED_METHODS": ["authenticator"],
        "SECURITY_TWO_FACTOR_RESCUE_EMAIL": False,
    },
    "unified sign-in without email": {
        "SECURITY_UNIFIED_SIGNIN": True,
        "SECURITY_US_ENABLED_METHODS": ["password", "authenticator"],
    },
}


@pytest.fixture
def mailless(site: SiteFactory) -> Flask:
    """A booted site with login and no mail transport, and every flask-security
    default in place -- so each test changes only the setting it names."""
    return site()


def test_every_row_of_the_table_has_a_case() -> None:
    """A row added for a new flask-security release without a test fails here."""
    assert set(MAILING) == {name for name, _ in auth.MAILING_FEATURES}


@pytest.mark.parametrize("feature", MAILING)
def test_each_mailing_feature_needs_a_transport(mailless: Flask, feature: str) -> None:
    mailless.config.update(MAILING[feature])
    with pytest.raises(RuntimeError, match=re.escape(feature)):
        auth.check_mail(mailless)


@pytest.mark.parametrize("case", SILENT)
def test_a_feature_that_sends_nothing_needs_nothing(mailless: Flask, case: str) -> None:
    mailless.config.update(SILENT[case])
    auth.check_mail(mailless)


@pytest.mark.parametrize("transport", ["mailman", "mail"])
def test_any_registered_transport_satisfies_it(mailless: Flask, transport: str) -> None:
    mailless.config.update(MAILING["CONFIRMABLE"])
    mailless.extensions[transport] = object()
    auth.check_mail(mailless)


def test_every_missing_feature_is_named_at_once(mailless: Flask) -> None:
    """One message, for the reason check_secrets gives: one name per restart
    is one rebuild per name."""
    mailless.config.update(MAILING["CONFIRMABLE"] | MAILING["RECOVERABLE"])
    with pytest.raises(RuntimeError) as caught:
        auth.check_mail(mailless)
    assert "CONFIRMABLE" in str(caught.value)
    assert "RECOVERABLE" in str(caught.value)


def test_a_site_whose_login_mails_will_not_start_without_mail(site: SiteFactory) -> None:
    with pytest.raises(RuntimeError, match="this site cannot start: its login sends mail"):
        site(config_overrides={"SECURITY_CONFIRMABLE": True})


def test_a_site_that_wires_its_own_mail_still_starts(site: SiteFactory) -> None:
    """The arrangement ADR-0025 described, which a site may keep: its own `init`
    registers the transport, before login and the apps are installed."""

    def wire(app: Flask) -> None:
        app.extensions["mailman"] = object()

    app = site(config_overrides={"SECURITY_CONFIRMABLE": True}, init=wire)
    assert app.config["SECURITY_CONFIRMABLE"] is True
