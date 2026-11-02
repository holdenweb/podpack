"""`flask users confirm`: the one account command flask-security lacks.

With confirmation on, flask-security refuses login to any account whose
`confirmed_at` is unset, and sets it only when the mailed link is followed. An
account made with `users create` -- `--active` or not -- could therefore never
sign in, and holdenweb.com's operator met exactly that. podpack adds `confirm`
to flask-security's own `users` group, beside `activate`.
"""

import click
import pytest
from click.testing import Result
from flask import Flask
from flask_security.cli import users as security_users
from flask_security.confirmable import requires_confirmation

from conftest import SiteFactory
from podpack.auth import user_datastore

EMAIL = "test-admin@example.com"


def flask(app: Flask, *args: str) -> Result:
    return app.test_cli_runner().invoke(args=list(args))


def still_unconfirmed(app: Flask) -> bool:
    """The test the login form applies before it says "Email requires
    confirmation." -- asked of flask-security itself, not re-implemented."""
    with app.app_context():
        return bool(requires_confirmation(user_datastore.find_user(email=EMAIL)))


def confirmed_at(app: Flask) -> object:
    """The account's stored timestamp, read in an app context of its own."""
    with app.app_context():
        user = user_datastore.find_user(email=EMAIL)
        assert user is not None, "the fixture's account has gone"
        return user.confirmed_at


@pytest.fixture
def confirming(site: SiteFactory) -> Flask:
    """A site with confirmation on, as holdenweb.com runs, holding one account
    made the way its operations.md makes the first one."""

    def wire(app: Flask) -> None:
        # Confirmation sends mail, so podpack will not start such a site
        # without a transport (ADR-0041). Nothing here sends any.
        app.extensions["mailman"] = object()

    app = site(
        config_overrides={
            "SECURITY_CONFIRMABLE": True,
            # The address is a test value; checking its domain's MX would make
            # the suite depend on DNS.
            "SECURITY_EMAIL_VALIDATOR_ARGS": {"check_deliverability": False},
        },
        init=wire,
    )
    created = flask(app, "users", "create", EMAIL, "--password", "test-a-long-password-9471", "--active")
    assert created.exit_code == 0, created.output
    return app


def test_an_account_made_by_users_create_cannot_sign_in(confirming: Flask) -> None:
    """The problem this command exists for, shown rather than asserted."""
    assert still_unconfirmed(confirming)


def test_confirm_lets_it_sign_in(confirming: Flask) -> None:
    result = flask(confirming, "users", "confirm", EMAIL)
    assert result.exit_code == 0, result.output
    assert f'User "{EMAIL}" has been confirmed.' in result.output
    assert not still_unconfirmed(confirming)


def test_confirming_twice_says_so_and_changes_nothing(confirming: Flask) -> None:
    flask(confirming, "users", "confirm", EMAIL)
    first = confirmed_at(confirming)
    result = flask(confirming, "users", "confirm", EMAIL)
    assert result.exit_code == 0, result.output
    assert f'User "{EMAIL}" was already confirmed.' in result.output
    assert confirmed_at(confirming) == first


def test_an_unknown_account_is_an_error(confirming: Flask) -> None:
    result = flask(confirming, "users", "confirm", "test-nobody@example.com")
    assert result.exit_code != 0
    assert "User not found." in result.output


def test_it_sits_beside_flask_securitys_own(confirming: Flask) -> None:
    result = flask(confirming, "users", "--help")
    assert result.exit_code == 0, result.output
    assert {"activate", "confirm", "create"} <= set(result.output.split())


def test_a_confirm_flask_security_ships_would_win(
    site: SiteFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Should a flask-security release add its own `users confirm`, podpack's
    steps aside. Simulated by putting another command there first; monkeypatch
    puts the shared group back afterwards."""
    theirs = click.Command("confirm", callback=lambda: None)
    monkeypatch.setitem(security_users.commands, "confirm", theirs)
    site()
    assert security_users.commands["confirm"] is theirs
