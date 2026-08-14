#!/bin/bash
# Run this site locally, with no containers at all.
#
# The sibling of scripts/up.sh, and the same site: it reads the same
# config/app.toml, installs the same apps in the same order, and applies the
# same alembic history. What differs is everything below the application --
# no image, no compose, no gunicorn, and a PostgreSQL you installed rather
# than one podpack started.
#
# It is deliberately not `up.sh --local`. The two share no code path, and a
# flag would suggest they do.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

if [[ ! -f dev.env ]]; then
    cp dev.env.example dev.env
    echo "created dev.env from dev.env.example -- review it before going further"
fi

# shellcheck disable=SC1091
set -a; . ./dev.env; set +a

# The roots podpack creates each app's directory under. Made here rather than
# lazily so a permissions problem shows up now rather than on whichever
# request first writes something -- the same reason prepare-host-dirs.sh
# exists for the container path.
mkdir -p "${PODPACK_DATA_ROOT}" "${PODPACK_LOG_ROOT}"

# Fail here, with the two commands that fix it, rather than several seconds
# later inside alembic with a connection error. podpack will not create the
# database itself: the server is yours, not its.
# Fail here, with the two commands that fix it, rather than several seconds
# later inside alembic with a connection error. Run through `uv run`, not the
# system python: psycopg2 is podpack's dependency and lives in the project's
# environment, and checking with an interpreter that lacks it reports the
# wrong problem convincingly.
#
# podpack will not create the database itself: the server is yours, not its.
if ! uv run python - "$SQLALCHEMY_DATABASE_URI" <<'PY'
import sys
from urllib.parse import urlsplit

import psycopg2

url = urlsplit(sys.argv[1])
try:
    psycopg2.connect(
        dbname=url.path.lstrip("/"), user=url.username, password=url.password,
        host=url.hostname or "127.0.0.1", port=url.port or 5432, connect_timeout=3,
    ).close()
except Exception as exc:
    print(f"    {exc}".rstrip(), file=sys.stderr)
    print(url.username or "", url.path.lstrip("/"), sep="\n", file=open("/tmp/.podpack-dev-db", "w"))
    sys.exit(1)
PY
then
    read -r user < /tmp/.podpack-dev-db
    name="$(sed -n 2p /tmp/.podpack-dev-db)"
    rm -f /tmp/.podpack-dev-db
    cat >&2 <<MSG

cannot reach the local database named in dev.env. If PostgreSQL is running,
it probably has no role or database for this site yet:

    createuser --pwprompt ${user}
    createdb --owner ${user} ${name}

Then run this script again. podpack does not create either: the server is
yours, and a run script that issued DDL against it would be doing something
you did not ask for.
MSG
    exit 2
fi

# The same history the migrate service applies in the container, against the
# same kind of server -- which is what makes a revision authored here safe to
# deploy. Never author one against SQLite.
uv run alembic upgrade head

if grep -q "compose.mongodb.yaml" .env 2>/dev/null; then
    cat >&2 <<'MSG'

note: this site declares mongodb, which a local run does not start. Install
one natively and add MONGODB_URI to dev.env, or expect the apps that use it
to report themselves unhealthy.
MSG
fi

echo
echo "development server -- one process, no gunicorn, no proxy, debug reloader on."
echo "the containerised stack is ./scripts/up.sh, and is what production resembles."
echo
exec uv run flask --app @@SITE_PACKAGE@@ run --debug --port "${FLASK_RUN_PORT:-5001}"
