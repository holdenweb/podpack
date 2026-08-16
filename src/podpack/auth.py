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

Mail and session policy stay the site's under ADR-0025, which is unchanged
except for its login clause. They are genuinely per-site: a site picks its own
mail server, or sends no mail.
"""

from flask import Flask
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

OWNED_TABLES = frozenset({"user", "role", "roles_users"})
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
