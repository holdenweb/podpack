"""Core services: the backing stores a site can choose to run.

A site is a config file, a list of installed apps -- and a list of the
services those apps store things in. PostgreSQL used to be welded into the
compose file; it is now one entry in this catalogue, and MongoDB is another.
Nothing here is special-cased: postgres is simply the service almost every
site happens to enable.

**A service declares one thing, its name, and the rest derives from it** --
the move ADR-0003 made for apps, applied to the substrate. `postgres`
satisfies that rule on nine of eleven derived names, which is the evidence
the rule was discovered rather than invented; its two exceptions are
declared here with their reasons, because a silent exception is how a rule
stops being one.

This module imports nothing from `podpack.substrate` and no database driver.
The direction is one way -- substrate reads the catalogue, never the reverse
-- so that the CLI can plan a site's files without a driver installed, and
`podpack.services.mongodb` can import pymongo lazily when a site actually
runs one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

BASE_COMPOSE = "compose.yaml"


@dataclass(frozen=True)
class CoreService:
    """One backing store a site may run, and everything that follows from it."""

    name: str
    """The identifier everywhere: catalogue key, compose service and hostname,
    overlay infix, the value an app puts in `SiteApp.requires`, the key in
    `[services.<name>]` and in `/_status`."""

    image: str
    internal_port: int
    default_host_port: int
    """Only the port forwarder's default. Nothing connects to it: services
    talk to each other on `internal_port` across the compose network."""

    storage_uid: int = 999
    """The unprivileged uid the server drops to, which its storage must be
    handed to before it starts -- see ADR-0020, the most expensive lesson in
    the project."""

    summary: str = ""
    driver: str = ""
    """The Python module a site needs installed to talk to this service.
    Empty when podpack's own dependencies already cover it."""

    extra: str = ""
    """The packaging extra that installs `driver`: `podpack[mongodb]`."""

    uri_env: str = ""
    """Override for the connection secret's name. Empty derives
    `<NAME>_URI`."""

    init_dir: str = ""
    """Override for the first-run bootstrap directory. Empty derives
    `<name>-init`."""

    # ---- derived: the rule ------------------------------------------------

    @property
    def overlay(self) -> str:
        """The compose file that adds this service, and the `depends_on` edges
        that make the rest of the stack wait for it."""
        return f"compose.{self.name}.yaml"

    @property
    def marker_env(self) -> str:
        """Stamped into `web` and `migrate` by the overlay, so podpack can
        check what compose *merged* rather than what a file *claimed*."""
        return f"PODPACK_SERVICE_{self.name.upper()}"

    @property
    def forwarder(self) -> str:
        """The socat service that publishes a host port on request (ADR-0027),
        under a compose profile of the same name."""
        return f"{self.name}-port"

    @property
    def storage_init(self) -> str:
        """This service's own storage-ownership one-shot. Per service, not
        shared: compose *replaces* `command:` when merging, so a single
        init container's chown is silently lost the moment an overlay
        touches it."""
        return f"init-{self.name}"

    @property
    def port_env(self) -> str:
        return f"{self.name.upper()}_HOST_PORT"

    @property
    def bind_env(self) -> str:
        return f"{self.name.upper()}_BIND_ADDR"

    @property
    def uri(self) -> str:
        return self.uri_env or f"{self.name.upper()}_URI"

    @property
    def bootstrap_dir(self) -> str:
        return self.init_dir or f"{self.name}-init"


POSTGRES = CoreService(
    name="postgres",
    image="docker.io/library/postgres:17",
    internal_port=5432,
    default_host_port=5433,
    summary="PostgreSQL, and the SQL database podpack's own `db` and alembic use",
    # No driver: psycopg2-binary is one of podpack's own dependencies, because
    # `db` is core rather than optional. See ADR-0028 on why SQL is the one
    # store an app may assume.
    #
    # Two declared exceptions to the derivation rule, each for a reason that
    # would cost more to remove than the inconsistency costs to keep:
    uri_env="SQLALCHEMY_DATABASE_URI",
    #   ^ named for the library that reads it. `POSTGRES_URI` would be tidier
    #     and would break flask-sqlalchemy, and the rename would have to
    #     happen inside `secrets.env` -- a file podpack may never rewrite.
    init_dir="db-init",
    #   ^ the directory every existing site already has. The engine has no
    #     verb that removes a file, so renaming it would strand a `db-init/`
    #     in three sites that `status` would never mention again.
)

MONGODB = CoreService(
    name="mongodb",
    image="docker.io/library/mongo:7",
    internal_port=27017,
    # 27017 is very likely a natively installed mongod and 27018 the old lab
    # that now lives in holdenweb.com's scratch/, so this is the third number.
    default_host_port=27019,
    summary="MongoDB, for apps that store documents rather than rows",
    driver="pymongo",
    extra="mongodb",
)

CATALOGUE: dict[str, CoreService] = {
    service.name: service for service in (POSTGRES, MONGODB)
}

DEFAULT_SERVICES: tuple[str, ...] = ("postgres",)
"""What a site that predates this catalogue is running, and what `init`
proposes. Not a claim that postgres is special -- only that every site so
far has one, and that a default nobody has to think about is worth more than
a symmetry nobody benefits from."""


def names() -> tuple[str, ...]:
    return tuple(CATALOGUE)


def unknown(declared: Sequence[str]) -> list[str]:
    """Declared names this podpack has never heard of.

    Reported rather than ignored: a typo in a service name would otherwise
    produce a site missing a database, diagnosed several layers away.
    """
    return sorted(set(declared) - set(CATALOGUE))


def compose_file_line(declared: Sequence[str]) -> str:
    """The value of COMPOSE_FILE for a site running exactly these services.

    Order follows the catalogue rather than the site's list, so that two
    sites running the same services produce the same line and a diff between
    them means something.
    """
    overlays = [CATALOGUE[name].overlay for name in CATALOGUE if name in declared]
    return ":".join([BASE_COMPOSE, *overlays])


def overlays_in(compose_file: str) -> list[str]:
    """The service names an actual COMPOSE_FILE value turns on."""
    parts = {part.strip() for part in compose_file.split(":")}
    return [name for name, service in CATALOGUE.items() if service.overlay in parts]
