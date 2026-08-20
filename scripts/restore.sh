#!/bin/bash
# Restore a podpack site from a backup taken by scripts/backup.sh.
#
# Written to be run by somebody who has never seen this site, from a checkout
# and a backup directory and nothing else. It does the mechanical parts and
# stops at the two places where judgement is actually required: whether this is
# the right target, and whether the code matches the data.
#
# Ordering is the part that is easy to get wrong and expensive to discover, so
# it is worth saying why the steps are in this order:
#
#   app data is unpacked BEFORE the first `compose up`, because init-storage
#   hands the bind mounts to the containers' unprivileged uids and, being gated
#   on `service_completed_successfully`, does not run again once it has
#   succeeded. Files unpacked after it are files the app cannot read.
#
#   each store comes up ALONE first, because its own first-run bootstrap
#   creates the application role -- and the dump's objects are owned by that
#   role. Bringing the whole stack up first would let `migrate` build an empty
#   schema the restore then has to demolish.
#
#   the rest of the stack comes up LAST, which makes `migrate` a free
#   consistency check: alembic either finds the restored revision already at
#   head and does nothing, or rolls an older backup forward onto newer code.
#
# Usage:  scripts/restore.sh <backup-directory> [--yes]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

backup="${1:-}"
assume_yes="${2:-}"
compose=(podman compose)

if [[ -z "$backup" || ! -d "$backup" ]]; then
    echo "usage: scripts/restore.sh <backup-directory> [--yes]" >&2
    exit 1
fi
for required in manifest.txt plan.json app-data.tar.gz env secrets.env; do
    if [[ ! -s "$backup/$required" ]]; then
        echo "backup is incomplete: $backup/$required is missing or empty" >&2
        exit 1
    fi
done

# The same stamp scripts/up.sh applies, for the same reason and one more. A
# restore onto a replacement host has no image yet, so compose builds one --
# and an unstamped build leaves /_status reporting `build_commit: unknown` on
# the deployment where "what code is this actually running?" matters most.
# Computed here rather than by calling up.sh, because a restore has to bring
# the stores up alone first and up.sh deliberately brings up everything.
if GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null)"; then
    git diff --quiet HEAD 2>/dev/null || GIT_SHA="${GIT_SHA}-dirty"
else
    GIT_SHA="unknown"
fi
export GIT_SHA

# ---------------------------------------------------------------------------
# Judgement point 1: is this the right backup, and the right target?
# ---------------------------------------------------------------------------
echo "================ restoring from ================"
cat "$backup/manifest.txt"
echo "================ restoring into ================"
echo "directory:        ${here}"
echo "checked out at:   $(git rev-parse HEAD 2>/dev/null || echo 'not a git checkout')"
echo "================================================"
echo

# ---------------------------------------------------------------------------
# Judgement point 2: does the code match the data?
#
# A warning rather than a refusal, and deliberately not an automatic
# `git checkout`: the operator may be restoring an old backup onto current code
# on purpose, which alembic handles, and clobbering somebody's working tree at
# 2am is a worse outcome than making them read a line of output.
# ---------------------------------------------------------------------------
want_commit="$(awk '/^git commit:/ {print $3}' "$backup/manifest.txt")"
have_commit="$(git rev-parse HEAD 2>/dev/null || echo UNKNOWN)"
if [[ "$want_commit" != "$have_commit" ]]; then
    echo "WARNING: this checkout is not the commit the backup was taken from."
    echo "  backup:   ${want_commit}"
    echo "  checkout: ${have_commit}"
    if git merge-base --is-ancestor "$want_commit" "$have_commit" 2>/dev/null; then
        echo "  The checkout is NEWER. alembic will roll the data forward; this is fine."
    else
        echo "  The checkout is OLDER or unrelated. The schema in this backup may"
        echo "  describe tables this code does not know about. Prefer:"
        echo "      git checkout ${want_commit}"
    fi
    echo
fi

if [[ "$assume_yes" != "--yes" ]]; then
    read -r -p "This will REPLACE the data and secrets above. Type 'restore' to go on: " reply
    [[ "$reply" == "restore" ]] || { echo "aborted"; exit 1; }
fi

# ---------------------------------------------------------------------------
# 1. Identity and host config, before anything reads them.
#
# secrets.env goes back verbatim, which is the whole of ADR-0013: change
# SECURITY_PASSWORD_SALT and every stored password becomes unverifiable, change
# the database identity and the site cannot reach its own data.
#
# .env is per-host, so a restore onto a *different* host wants the existing one
# kept and edited instead. Both are preserved beside their replacements rather
# than overwritten, because getting this wrong at 2am should be recoverable.
# ---------------------------------------------------------------------------
suffix="superseded-$(date -u +%Y%m%dT%H%M%SZ)"
for f in .env secrets.env; do
    [[ -f "$f" ]] && cp "$f" "${f}.${suffix}" && echo "kept the existing ${f} as ${f}.${suffix}"
done
install -m 600 "$backup/env" .env
install -m 600 "$backup/secrets.env" secrets.env
# shellcheck disable=SC1091
set -a; . ./.env; set +a

# config/ is in git and normally already correct. It is restored anyway because
# app.toml's app list decides which tables the schema is supposed to have, and
# a restore against a drifted app list is the one mismatch nothing downstream
# catches -- autogenerate would later propose dropping the orphaned tables.
[[ -f "$backup/config.tar.gz" ]] && tar -xzf "$backup/config.tar.gz" -C .

# ---------------------------------------------------------------------------
# 2. Host directories and app data, before the first `compose up`.
# ---------------------------------------------------------------------------
./scripts/prepare-host-dirs.sh >/dev/null
echo "unpacking app data into ${HOST_DATA_DIR}/apps ..."
tar -xzf "$backup/app-data.tar.gz" -C "${HOST_DATA_DIR}/apps"
[[ -f "$backup/app-extra.tar.gz" ]] && tar -xzf "$backup/app-extra.tar.gz" -C "${HOST_DATA_DIR}"

# ---------------------------------------------------------------------------
# 3. Each store alone, so its bootstrap creates the role the dump's objects
#    belong to. On a machine that has run this site before the data directory
#    is not empty and the bootstrap does not re-run -- which is correct,
#    because the role is already there.
# ---------------------------------------------------------------------------
# A read loop rather than `mapfile`: macOS ships bash 3.2, where mapfile does
# not exist, and the script would die here having already taken a good dump.
services=()
while IFS= read -r line; do services+=("$line"); done < <(python3 -c '
import json, sys
for service in json.load(open(sys.argv[1]))["services"]:
    print(service["name"])
' "$backup/plan.json")

for service in "${services[@]}"; do
    echo "starting ${service} ..."
    "${compose[@]}" up -d "$service"
    printf 'waiting for %s to report healthy' "$service"
    status=starting
    for _ in $(seq 60); do
        status="$(podman inspect --format '{{.State.Health.Status}}' \
            "${SITE_NAME}-${service}-1" 2>/dev/null || echo starting)"
        [[ "$status" == "healthy" ]] && break
        printf '.'
        sleep 2
    done
    echo
    if [[ "$status" != "healthy" ]]; then
        echo "${service} did not become healthy; see 'podman compose logs ${service}'" >&2
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# 4. The data. Each store's own restore command, run in its own container, with
#    the credentials coming from that container's environment.
# ---------------------------------------------------------------------------
while IFS=$'\t' read -r service file restore; do
    [[ -n "$service" ]] || continue
    echo "restoring ${service} from ${file} ..."
    "${compose[@]}" exec -T "$service" sh -c "$restore" < "${backup}/${file}" >/dev/null
done < <(python3 -c '
import json, sys
for service in json.load(open(sys.argv[1]))["services"]:
    print("\t".join([service["name"], service["file"], service["restore"]]))
' "$backup/plan.json")

# ---------------------------------------------------------------------------
# 5. The rest of the stack. `migrate` runs here and is the consistency check.
# ---------------------------------------------------------------------------
echo "starting the rest of the stack ..."
"${compose[@]}" up -d --build

# ---------------------------------------------------------------------------
# 6. Verify, out loud. A restore that is not checked is a restore that has not
#    happened.
# ---------------------------------------------------------------------------
echo
echo "================ verification =================="
counts_ok=yes
if [[ -s "$backup/rowcounts.txt" ]]; then
    # Tables coming back is not evidence that the data did: an empty dump
    # restores cleanly and leaves a site that looks structurally perfect and
    # has lost everything.
    echo "tables restored:"
    while IFS='|' read -r schema table expected; do
        [[ -n "$table" ]] || continue
        actual="$("${compose[@]}" exec -T postgres sh -c \
            "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc 'select count(*) from \"$schema\".\"$table\"'" \
            2>/dev/null | tr -d '\r' || echo MISSING)"
        if [[ "$actual" == "$expected" ]]; then
            printf '  %-28s %s rows\n' "${schema}.${table}" "$actual"
        else
            printf '  %-28s MISMATCH: expected %s, found %s\n' "${schema}.${table}" "$expected" "$actual"
            counts_ok=no
        fi
    done < "$backup/rowcounts.txt"
fi

printf 'site healthz: '
code=000
for _ in $(seq 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' \
        "http://${WEB_BIND_ADDR:-127.0.0.1}:${WEB_HOST_PORT}/healthz" || true)"
    [[ "$code" == "200" ]] && break
    sleep 2
done
echo "$code  (http://${WEB_BIND_ADDR:-127.0.0.1}:${WEB_HOST_PORT}/)"
echo "================================================"

[[ "$code" == "200" ]] || {
    echo "the site is not healthy -- see 'podman compose logs web'" >&2; exit 1; }
[[ "$counts_ok" == "yes" ]] || {
    echo "row counts do not match the backup -- the data is NOT fully restored" >&2; exit 1; }
echo "restore complete, and checked."
