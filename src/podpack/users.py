"""Creating the people who may sign in, and the one role podpack cares about.

Every site needs an administrator before anybody can read `/_status`, and a
site with no way to make one is a site whose operator view is unreachable.
So the commands live here rather than being reinvented per site.

podpack still owns no login. It does not import flask-security, define a
`User` model or touch a `Role` table directly: ADR-0025 leaves all of that
to the site, and this module only asks the site for the datastore it already
registered. What podpack contributes is the *name* -- `admin` is the role
its own guard asks about, so it is the one identity fact a framework that
ships an operator endpoint has to have an opinion on.

These are Flask CLI commands rather than subcommands of the `podpack`
console script, and that is the difference between needing a running
application and not. `podpack substrate` plans files and needs no app;
creating a user needs the site's models, its database and its password
hashing, all of which arrive with the factory. Flask already solves
"find the app" with `--app`, so there is nothing to invent:

    flask --app holdenweb podpack users create steve@holdenweb.com --admin

**Not the `users` group**, which flask-security already ships with its own
`create`, `activate`, `deactivate`, `change_password` and `reset_access`.
Registering a second group of that name does not merge them: one silently
replaces the other, and which one wins depends on registration order. So
podpack's live under `podpack`, flask-security's stay where its own
documentation says they are, and neither hides the other.

What these add over flask-security's is the whole point of them: making the
first administrator in one command. `flask users create` cannot grant a
role, `flask roles add` needs the role to exist, and `admin` does not exist
in a fresh database -- so the thing every new site needs was three commands
and knowing the order.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import click
from flask import current_app
from flask.cli import AppGroup, with_appcontext

ADMIN_ROLE = "admin"
"""The role `/_status` asks about.

A site's `admin` predicate need not use it -- podpack asks the site a
question and does not care how it is answered (`create_app(admin=...)`) --
but a site with no reason to differ should use this, and these commands
grant it.
"""

podpack_cli = AppGroup("podpack", help="podpack's own commands for this site.")
users_cli = AppGroup("users", help="Create and inspect the people who may sign in.")
podpack_cli.add_command(users_cli)


def _datastore() -> Any:
    """The site's user datastore, or a refusal that says what is missing.

    Found rather than required: flask-security stashes itself at
    `app.extensions["security"]` when the site calls `init_app`, so podpack
    can use what a site already wired without depending on it. A site that
    wires a different login system entirely will not have this, and gets a
    sentence rather than an AttributeError.
    """
    security = current_app.extensions.get("security")
    datastore = getattr(security, "datastore", None)
    if datastore is None:
        raise click.ClickException(
            "this site wires no user datastore, so podpack has nobody to "
            "create. Login belongs to the site (ADR-0025): wire "
            "flask-security in the `init` you pass to create_app, and these "
            "commands will find it."
        )
    return datastore


def _hash(password: str) -> str:
    """Hash with whatever the site's login system uses.

    Imported here rather than at module level: podpack does not depend on
    flask-security, and a site that wires none never reaches this line.
    """
    from flask_security import hash_password

    return str(hash_password(password))


@users_cli.command("create")
@click.argument("email")
@click.option("--admin", is_flag=True,
              help=f"also grant the {ADMIN_ROLE!r} role, which `/_status` asks about")
@click.option("--password-stdin", is_flag=True,
              help="read the password from standard input instead of prompting")
@with_appcontext
def create_user(email: str, admin: bool, password_stdin: bool) -> None:
    """Create a user who can sign in to this site.

    The password is prompted for, hidden and confirmed, or read from stdin
    for automation. Deliberately not an option on the command line: a
    password given that way is in the shell history, in the process list,
    and often in a CI log, and none of those forget.
    """
    datastore = _datastore()
    if datastore.find_user(email=email):
        raise click.ClickException(f"{email} already exists; `users grant` adds a role")

    if password_stdin:
        password = click.get_text_stream("stdin").readline().strip()
        if not password:
            raise click.ClickException("no password arrived on stdin")
    else:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

    datastore.create_user(
        email=email,
        password=_hash(password),
        active=True,
        # Harmless where confirmation is off, and the difference between a
        # working login and a silent refusal where it is on. An operator
        # creating an account at the console has done the confirming.
        confirmed_at=datetime.now(timezone.utc),
    )
    if admin:
        datastore.add_role_to_user(email, _role(datastore, ADMIN_ROLE))
    datastore.commit()
    click.echo(f"created {email}" + (f" as {ADMIN_ROLE}" if admin else ""))


@users_cli.command("grant")
@click.argument("email")
@click.argument("role", default=ADMIN_ROLE)
@with_appcontext
def grant_role(email: str, role: str) -> None:
    """Grant ROLE (default: admin) to an existing user."""
    datastore = _datastore()
    user = datastore.find_user(email=email)
    if user is None:
        raise click.ClickException(f"no such user: {email}")
    datastore.add_role_to_user(user, _role(datastore, role))
    datastore.commit()
    click.echo(f"{email} is now {role}")


@users_cli.command("list")
@with_appcontext
def list_users() -> None:
    """List the users of this site and the roles they hold."""
    from podpack import db

    datastore = _datastore()
    users = db.session.scalars(db.select(datastore.user_model)).all()
    if not users:
        click.echo("no users yet -- `flask users create <email> --admin` makes the first")
        return
    for user in users:
        roles = ", ".join(sorted(role.name for role in user.roles)) or "-"
        click.echo(f"{user.email:40} {roles}")


def _role(datastore: Any, name: str) -> Any:
    """The named role, created if this site has never had one.

    `admin` does not exist in a fresh database, so the first grant is also
    the moment the role is born -- which is why creating the first
    administrator is one command rather than three.
    """
    description = (
        "May read /_status and anything else the site reserves for operators"
        if name == ADMIN_ROLE
        else None
    )
    return datastore.find_or_create_role(name=name, description=description)


def register(app: Any) -> None:
    app.cli.add_command(podpack_cli)
