#!/bin/bash
# Runs exactly once: the image's entrypoint only executes this directory while
# the data directory is still empty, which -- because the data directory lives
# on the host -- means "the first time you bring this lab up on this machine".
#
# Its job is to create the least-privileged role the application connects with,
# so the admin credentials never leave .env and the bootstrap.
set -euo pipefail

echo "creating application role '${POSTGRES_APP_USER}' in database '${POSTGRES_DB}'"

psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
     --set ON_ERROR_STOP=1 --no-password <<-SQL
	CREATE ROLE ${POSTGRES_APP_USER} LOGIN PASSWORD '${POSTGRES_APP_PASSWORD}';
	GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_APP_USER};

	-- The app owns its own schema rather than being given rights over
	-- public. That way it can create its tables without any privilege on
	-- the rest of the database, and PostgreSQL 15+'s locked-down public
	-- schema stays locked down.
	CREATE SCHEMA app AUTHORIZATION ${POSTGRES_APP_USER};

	-- So unqualified table names land in that schema. Applied to new
	-- connections, which is every connection the app will ever make.
	ALTER ROLE ${POSTGRES_APP_USER} SET search_path = app;

	-- Not strictly needed, but makes the intent explicit: no write access
	-- to public, for anyone but its owner.
	REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL

echo "application role created"
