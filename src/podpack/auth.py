"""Login, which every site turns out to need.

podpack asserted most of this for a while without shipping any of it. It named
the role (`ADMIN_ROLE`), guarded `/_status` with a predicate whose shape it
defined, called `datastore.find_role(ADMIN_ROLE)` at boot to warn when nobody
could read that report, and gave the README a section on three flask-security
commands. What it left to each site was the twenty-four lines that turn those
assertions into a working login -- identical twenty-four lines, in every site
that wrote them.

ADR-0033 has the argument. The short version is ADR-0029's, one layer up: a
thing the framework's own endpoints require is not optional, and calling it
optional produces sites that cannot do what the framework documents. Two of
ours could never answer `/_status` at all.

Session policy stays the site's under ADR-0025, and so does what a mail failure
says -- the class named in `PODPACK_MAIL_UTIL_CLS`. The mail transport itself
became an app under ADR-0041, which is why `check_mail` is here: login decides
whether a site sends mail, an app decides whether it can, and only podpack
installs both.
"""

import functools
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from flask import Flask, abort, current_app
from flask_security import Security, SQLAlchemyUserDatastore, current_user
from flask_security.models import fsqla_v3 as fsqla

from .database import db

ADMIN_ROLE = "admin"
"""The role `/_status` asks about, and the one every site needs first.

    flask --app <site> users create you@example.com --active
    flask --app <site> roles create admin
    flask --app <site> roles add you@example.com admin

Those are flask-security's own commands, which arrive with it. podpack ships no
copy of them: they validate the identity through the registration form and
resolve users the way the rest of flask-security does, so a framework version
could only be a worse one.
"""

NEEDED_TABLES = frozenset({"user", "role", "roles_users"})
"""The tables this module puts on `db.metadata`.

Recorded because attribution is by *defining module* and podpack is not an
installed app, so nothing would otherwise answer for them -- and
`/_status`'s `unclaimed.tables` would report all three as belonging to nobody.
`roles_users` is here for a second reason: it is built inside flask-security
rather than in this module, so even attribution by defining module would miss
it. See ADR-0032, which is where that hole was found.

The names are flask-security's own, derived from its mixins and assumed by its
datastore and its documentation. They are unprefixed, and deliberately so: an
app that wants a `user` table of its own has to say so, and now collides with
the framework rather than with whichever site got there first.
"""

fsqla.FsModels.set_db_info(db)


class Role(db.Model, fsqla.FsRoleMixin):  # type: ignore[name-defined]
    pass


class User(db.Model, fsqla.FsUserMixin):  # type: ignore[name-defined]
    pass


user_datastore = SQLAlchemyUserDatastore(db, User, Role)


def is_admin() -> bool:
    """Whether the current request is an operator's.

    Membership of the `admin` role, not merely being signed in: `/_status`
    names the database, the role it connects as and every host path, which is
    operator business rather than user business.

    The role has to exist and be granted before anybody qualifies. A fresh
    database has neither, so `/_status` answers 404 until somebody sets that
    up -- which is the right way round, and which podpack now says at boot
    rather than leaving to be discovered.
    """
    return bool(current_user.is_authenticated and current_user.has_role(ADMIN_ROLE))


_P = ParamSpec("_P")
_R = TypeVar("_R")


def is_operator() -> bool:
    """Whether this request is an operator's, through the site's own predicate.

    `is_admin` unless the site replaced it (ADR-0033), reached through
    `extensions["podpack"].admin` so that the replacement is honoured. An
    exception in it counts as a refusal, and so does `None`: a guard that fails
    open is not a guard.
    """
    admin = current_app.extensions["podpack"].admin
    if admin is None:
        return False
    try:
        return bool(admin())
    except Exception:  # noqa: BLE001 -- a broken guard denies, it does not admit
        return False


def operator_required(view: Callable[_P, _R]) -> Callable[_P, _R]:
    """Refuse a route to anyone who is not an operator, with a 404.

    Two things make this worth a decorator of podpack's own rather than
    flask-security's `roles_required`, and both are about what a refusal says:

    * **404, not 403.** A 403 confirms the route exists, and that this is a
      podpack site at all -- information an operator has no reason to publish.
      `/_status` has answered 404 since it was written, for that reason.
    * **The site's predicate, not the role directly.** `roles_required` asks
      flask-security about `admin` membership and so walks straight past a
      predicate the site replaced under ADR-0033, which is the one thing a site
      is entitled to change here.

    It was hand-rolled in two places before this existed -- podpack's own
    `/_status` and holdenweb.com's `/config` -- which is one copy more than a
    guard should ever have.
    """

    @functools.wraps(view)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if not is_operator():
            abort(404)
        return view(*args, **kwargs)

    return wrapper


def install(app: Flask, mail_util_cls: type | None = None) -> None:
    """Attach login to a site.

    `mail_util_cls` is passed through because a site that sends its own mail
    will want to say what happens when the mail does not go -- flask-security
    catches nothing its backend raises, so the default turns a dead SMTP
    credential into a 500 on the password-reset form. It goes to the
    constructor rather than to `init_app`, which flask-security deprecated in
    5.6.1 and warns about.

    A `Security` per app rather than one unbound instance reused: several
    sites are built in one interpreter by every test suite and by no
    deployment, and flask-security keeps its state in `app.extensions`
    anyway, so per-app is both simpler and the safer of the two.
    """
    extra = {} if mail_util_cls is None else {"mail_util_cls": mail_util_cls}
    Security(app=app, datastore=user_datastore, **extra)  # type: ignore[arg-type]


MAILING_FEATURES: tuple[tuple[str, Callable[[Callable[[str], Any]], Any]], ...] = (
    ("CONFIRMABLE", lambda cv: cv("CONFIRMABLE")),
    (
        "REGISTERABLE with SEND_REGISTER_EMAIL",
        lambda cv: cv("REGISTERABLE") and cv("SEND_REGISTER_EMAIL"),
    ),
    (
        "RECOVERABLE",
        lambda cv: cv("RECOVERABLE")
        and (cv("SEND_PASSWORD_RESET_EMAIL") or cv("SEND_PASSWORD_RESET_NOTICE_EMAIL")),
    ),
    (
        "CHANGEABLE with SEND_PASSWORD_CHANGE_EMAIL",
        lambda cv: cv("CHANGEABLE") and cv("SEND_PASSWORD_CHANGE_EMAIL"),
    ),
    ("CHANGE_EMAIL", lambda cv: cv("CHANGE_EMAIL")),
    (
        "CHANGE_USERNAME with SEND_USERNAME_CHANGE_EMAIL",
        lambda cv: cv("CHANGE_USERNAME") and cv("SEND_USERNAME_CHANGE_EMAIL"),
    ),
    ("USERNAME_RECOVERY", lambda cv: cv("USERNAME_RECOVERY")),
    ("PASSWORDLESS", lambda cv: cv("PASSWORDLESS")),
    (
        "TWO_FACTOR, by email or with its rescue mail",
        lambda cv: cv("TWO_FACTOR")
        and ({"email", "mail"} & set(cv("TWO_FACTOR_ENABLED_METHODS") or ()) or cv("TWO_FACTOR_RESCUE_EMAIL")),
    ),
    (
        "UNIFIED_SIGNIN by email",
        lambda cv: cv("UNIFIED_SIGNIN") and "email" in (cv("US_ENABLED_METHODS") or ()),
    ),
)
"""What flask-security sends mail for, and the settings that make it do so.

Read off its `send_mail` call sites in 5.8.2 rather than from memory or its
documentation: each is a feature flag, and some carry a `SEND_*` switch of
their own. A table, because flask-security has no question of its own to ask
-- a release that adds a mailing feature adds a row here, and a test."""


def check_mail(app: Flask) -> None:
    """Refuse to start a site whose login sends mail that nothing can carry.

    flask-security finds its transport per send and, with none, raises
    `ValueError("No email extension configured")` -- on the first registration
    or password reset, in front of a visitor. Which features send mail is the
    site's configuration; what carries it is an app (ADR-0041); neither can see
    the other, so podpack, which installs both, is the one place to check.

    Any registered transport satisfies it: flask-mailman's `mailman`, or
    Flask-Mail's `mail`, which flask-security also accepts. Whether that
    transport can log in to its server is `flask mail check`'s business, not
    boot's -- boot runs on every restart, and a mail server is somebody else's.
    """
    if "mailman" in app.extensions or "mail" in app.extensions:
        return

    def cv(name: str) -> Any:
        return app.config.get(f"SECURITY_{name}")

    sending = [name for name, sends in MAILING_FEATURES if sends(cv)]
    if sending:
        raise RuntimeError(
            "this site cannot start: its login sends mail ("
            + "; ".join(sending)
            + ") and nothing is installed to send it. Add a mail app such as "
            "podpack_mail to [site] apps, or turn those features off."
        )
