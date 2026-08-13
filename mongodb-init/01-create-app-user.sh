#!/bin/bash
# Runs exactly once: the image's entrypoint executes this directory only while
# the data directory is still empty -- and because that directory lives on the
# host, "once" means the first time this suite comes up on this machine, not
# each time the container is recreated. Re-arming it means deleting the host
# data directory, exactly as it does for PostgreSQL.
#
# Its job is the same as db-init's: create the least-privileged user the
# application connects as, so the root credentials are used exactly once and
# never given to the app.
set -euo pipefail

echo "creating application user '${MONGODB_APP_USER}' in database '${MONGODB_DB}'"

mongosh --quiet \
    --host 127.0.0.1 \
    --username "${MONGO_INITDB_ROOT_USERNAME}" \
    --password "${MONGO_INITDB_ROOT_PASSWORD}" \
    --authenticationDatabase admin \
    "${MONGODB_DB}" \
    --eval "db.createUser({
        user: '${MONGODB_APP_USER}',
        pwd: '${MONGODB_APP_PASSWORD}',
        roles: [{role: 'readWrite', db: '${MONGODB_DB}'}]
    })"

echo "application user created"
