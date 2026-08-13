#!/bin/bash
# Create the host directories the suite bind-mounts, and copy .env into place
# on first run. Safe to re-run.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

# Two files, split by what restoring them means. `.env` is per-host and expected
# to be edited; `secrets.env` must be restored verbatim from a backup, and
# generating a fresh one is only right for a site that has no data yet.
for f in .env secrets.env; do
    if [[ ! -f "$f" ]]; then
        cp "${f}.example" "$f"
        echo "created ${f} from ${f}.example -- review it before going further"
    fi
done

# Only .env is sourced here: this script needs the host paths and nothing else,
# and reading credentials it has no use for would be gratuitous.
# shellcheck disable=SC1091
set -a; . ./.env; set +a

# Note what is *not* here, in either direction.
#
# $HOST_DATA_DIR/postgres/pgdata is absent because PostgreSQL insists its data
# directory be mode 0700, and a bind mount point's permissions belong to the
# host -- so initdb creates that sub-directory itself, inside the mount.
#
# The per-app directories under apps/ are absent because podpack creates them
# at startup, named after whatever is installed. That is the point of mounting
# the roots: installing an app must never require a change here or to
# compose.yaml.
dirs=("${HOST_DATA_DIR}/apps" "${HOST_LOG_DIR}/apps")

# Per-service directories, derived from the overlays COMPOSE_FILE names -- so
# enabling a service in .env is the whole of enabling it, with no second list
# here to fall out of step. A service podpack does not know about is simply
# not matched, which is the same silence compose gives it.
case "${COMPOSE_FILE:-}" in
    *compose.postgres.yaml*)
        dirs+=("${HOST_DATA_DIR}/postgres" "${HOST_LOG_DIR}/postgres") ;;
esac
case "${COMPOSE_FILE:-}" in
    *compose.mongodb.yaml*)
        dirs+=("${HOST_DATA_DIR}/mongodb/data" "${HOST_DATA_DIR}/mongodb/configdb"
               "${HOST_LOG_DIR}/mongodb") ;;
esac

for dir in "${dirs[@]}"
do
    mkdir -p "$dir"
    echo "ready: $dir"
done

# The containers run unprivileged, as uid 999 (postgres, fixed by the upstream
# image) and uid 10001 (the app, fixed by our Containerfile). Under rootless
# podman those uids land inside your user namespace rather than on real host
# uids, so the directories only need to be writable by the mapped user -- which
# `podman unshare chown` arranges. On Linux this is required; on macOS the
# virtiofs mount already presents everything as writable, and the compose
# init-storage service handles it in either case.
if [[ "$(uname -s)" == "Linux" ]]; then
    podman unshare chown -R 10001:10001 "${HOST_DATA_DIR}/apps" "${HOST_LOG_DIR}/apps"
    for dir in "${dirs[@]}"; do
        case "$dir" in
            *"/apps") ;;
            *) podman unshare chown -R 999:999 "$dir" ;;
        esac
    done
    echo "ownership set for the containers' unprivileged uids"
fi
