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

    optional: bool = True
    """Whether a site may choose not to run it.

    PostgreSQL is not. podpack requires `SQLALCHEMY_DATABASE_URI` before it
    will build an application at all, `db` and its single alembic history are
    core, and the site's own login tables live there -- so a site without SQL
    is not a smaller podpack site, it is one that cannot start.

    What remains optional is the *container*: dropping this overlay from
    COMPOSE_FILE by hand and pointing the URI at a managed PostgreSQL is
    supported, and is the escape hatch ADR-0015 asked for. podpack will not
    do it for you, exactly as it removes no other service."""

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

    dump: str = ""
    """How to take a consistent snapshot of this store.

    A shell command rather than an argv list, run inside the service's own
    container as `sh -c`. That is what lets the credentials arrive from the
    container's own environment instead of from a process argument, where
    `ps` on a shared host would show them to anybody looking.

    A snapshot, never a copy of the data directory: copying a live store
    produces a torn image that may or may not recover on start, and the
    failure is silent until the day it matters."""

    restore: str = ""
    """The other direction, under the same rules."""

    verify: str = ""
    """How to read a dump right through without applying any of it.

    The cheap check that turns a backup from a claim into evidence. Both
    stores have one because both formats are parsed rather than streamed, so
    a truncated or corrupt archive fails here -- at the moment the backup is
    taken, when the original still exists -- instead of during the restore
    that needed it."""

    dump_file: str = ""
    """What the snapshot is called inside a backup directory."""

    # Those three are declared per service rather than derived, and the rule
    # this catalogue is built on is exactly why that needs saying: nothing
    # turns `postgres` into `pg_dump`, or `mongodb` into `mongodump`. They
    # join `uri_env` and `init_dir` as exceptions carrying their reason,
    # which is this file's own convention for a rule that does not reach.

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
    optional=False,
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
    #
    # The whole database, not `--schema=app`: a filter would silently drop
    # anything a future app puts elsewhere, and the cost of carrying it is
    # a few kilobytes. Owners are kept, deliberately -- `db-init` recreates
    # the application role under the same name on the way back in, and the
    # app owning its own schema is what lets it create tables without any
    # privilege over the rest of the database. Restoring ownerless hands
    # the schema to the admin role and breaks the next migration.
    dump='pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom',
    # Custom format rather than SQL, because it makes verification free:
    # `pg_restore --list` parses the entire archive, so a truncated one
    # fails when the backup is taken rather than when it is needed.
    restore=(
        'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
        " --clean --if-exists --exit-on-error"
    ),
    #   ^ --clean --if-exists because the database a restore lands in is
    #     never empty: db-init has already created the role and the schema,
    #     and a second restore lands on the first one's objects. --exit-on-
    #     error is not optional either -- without it pg_restore reports every
    #     failure and still exits 0, so a restore that restored nothing looks
    #     exactly like one that worked.
    dump_file="database.pgc",
    verify="pg_restore --list",
    #   ^ parses the whole archive and prints its table of contents, so it
    #     fails on a truncated one; and the listing doubles as the evidence
    #     that the dump holds table data rather than an empty schema.
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
    # The root credentials, because a backup has to read everything and the
    # application role deliberately cannot. `--archive` writes one stream to
    # stdout, which is what lets this be piped out of the container exactly
    # as pg_dump is, with no shared volume between them.
    dump=(
        'mongodump --username "$MONGO_INITDB_ROOT_USERNAME"'
        ' --password "$MONGO_INITDB_ROOT_PASSWORD"'
        " --authenticationDatabase admin --archive --gzip"
    ),
    restore=(
        'mongorestore --username "$MONGO_INITDB_ROOT_USERNAME"'
        ' --password "$MONGO_INITDB_ROOT_PASSWORD"'
        " --authenticationDatabase admin --archive --gzip --drop"
    ),
    dump_file="mongodb.archive.gz",
    verify=(
        'mongorestore --username "$MONGO_INITDB_ROOT_USERNAME"'
        ' --password "$MONGO_INITDB_ROOT_PASSWORD"'
        " --authenticationDatabase admin --archive --gzip --dryRun"
    ),
)

CATALOGUE: dict[str, CoreService] = {
    service.name: service for service in (POSTGRES, MONGODB)
}

def required() -> tuple[str, ...]:
    """The services every site runs, whatever it declares."""
    return tuple(name for name, service in CATALOGUE.items() if not service.optional)


def optional_names() -> tuple[str, ...]:
    """The services a site may choose to run."""
    return tuple(name for name, service in CATALOGUE.items() if service.optional)


DEFAULT_SERVICES: tuple[str, ...] = required()
"""What a new site runs before it asks for anything: the mandatory set."""


def normalise(declared: Sequence[str]) -> tuple[str, ...]:
    """The services a site actually runs, in catalogue order.

    Mandatory services are included whether or not they were declared: a
    site's list records what it *chose*, and choosing is only meaningful
    where there is a choice.
    """
    wanted = set(declared) | set(required())
    return tuple(name for name in CATALOGUE if name in wanted)


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
    overlays = [CATALOGUE[name].overlay for name in normalise(declared)]
    return ":".join([BASE_COMPOSE, *overlays])


def overlays_in(compose_file: str) -> list[str]:
    """The service names an actual COMPOSE_FILE value turns on."""
    parts = {part.strip() for part in compose_file.split(":")}
    return [name for name, service in CATALOGUE.items() if service.overlay in parts]
