#!/bin/bash
# Take a complete, self-describing backup of a running podpack site.
#
# "Complete" is the whole claim, so it is worth saying what it rests on. A
# podpack site keeps its entire mutable state in three places and nowhere else
# -- its backing stores, the per-app data directories, and the two env files --
# and that is a consequence of the substrate's design rather than a
# coincidence: containers own no state, host config is mounted read-only,
# secrets live in the environment. Everything else is in git and comes back by
# checking out the right commit.
#
# What it archives is not this script's opinion. `podpack backup plan` reports
# what the installed apps say about themselves and which stores compose
# actually merged, and this executes that. So an app that stores nothing is
# skipped because it said so, a site that runs MongoDB has its documents dumped
# without anybody editing this file, and adding an app to `[site] apps` changes
# what a backup contains with no change here at all.
#
# What makes the result restorable by somebody who has never seen the site is
# manifest.txt. Data alone is not enough, because podpack's schema is a
# function of the *app list*: there is one alembic history covering whichever
# apps are enabled, so a dump restored against a different app list leaves
# tables no app claims -- and the next autogenerate faithfully proposes
# dropping them. The manifest records the three facts that must agree on the
# way back in: the commit, the app list, and the alembic revision.
#
# Usage:  scripts/backup.sh [destination-directory]
# Default destination: $BACKUP_ROOT, or ~/backups/<SITE_NAME>.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

if [[ ! -f .env ]]; then
    echo "no .env -- nothing to back up; this is not a deployed site" >&2
    exit 1
fi
# shellcheck disable=SC1091
set -a; . ./.env; set +a

compose=(podman compose)
project="${SITE_NAME:-podpack}"

# A timestamp to the second, so two backups in one day cannot collide and the
# lexical order of a directory listing is the chronological order.
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
root="${BACKUP_ROOT:-${HOME}/backups/${project}}"
dest="${1:-$root}/${stamp}"

# Outside the working tree by default, and refused inside it: this directory
# ends up holding a verbatim copy of every secret the site has, and a directory
# of credentials inside a repository is one careless `git add` away from being
# published.
case "$(cd "$(dirname "${1:-$root}")" && pwd)/" in
    "${here}/"*) echo "refusing to write a backup inside the site directory:" >&2
                 echo "  it would contain secrets.env in clear. Set BACKUP_ROOT." >&2
                 exit 1 ;;
esac

if ! "${compose[@]}" ps --status running --services 2>/dev/null | grep -qx web; then
    echo "the web service is not running, so nothing can say what to back up" >&2
    echo "bring the site up, or restore from an existing backup instead" >&2
    exit 1
fi

# The containers were found by compose project name, and a name is not proof of
# identity. podpack is single-site by design, so a host running several sites
# runs several deployments -- and a checkout copied or renamed keeps the
# compose project name of the tree it came from. Both then answer to the same
# names, and a backup that dumps one deployment's database beside another's app
# data looks complete, restores without error, and produces a site whose schema
# does not match its content. pp-testing's copy of this found exactly that, in
# its own repository, the first time it ran.
data_root_abs="$(cd "${HOST_DATA_DIR}" && pwd)"
mounted_from="$(
    podman inspect --format \
        '{{range .Mounts}}{{if eq .Destination "/var/lib/'"${project%%-*}"'/apps"}}{{.Source}}{{end}}{{end}}' \
        "${project}-web-1" 2>/dev/null || true
)"
if [[ -n "$mounted_from" && "$mounted_from" != "${data_root_abs}/apps" ]]; then
    echo "REFUSING: '${project}-web-1' does not belong to this directory." >&2
    echo "  its app data:      ${mounted_from}" >&2
    echo "  this deployment's: ${data_root_abs}/apps" >&2
    echo "Another deployment is using this compose project name." >&2
    exit 1
fi

mkdir -p "$dest"
chmod 700 "$(dirname "$dest")" "$dest"
echo "backup: ${dest}"

# ---------------------------------------------------------------------------
# What this site is made of, asked of the site rather than assumed.
#
# Run inside `web` because that is where the apps are installed; it needs no
# running application, only importable packages. The plan carries names and
# subpaths and never absolute paths, because the container's data root is not
# the host's -- this script joins the names to its own.
# ---------------------------------------------------------------------------
"${compose[@]}" exec -T web podpack backup plan > "$dest/plan.json"
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$dest/plan.json" \
    || { echo "podpack backup plan did not return usable JSON" >&2; exit 1; }

if ! python3 -c '
import json, sys
plan = json.load(open(sys.argv[1]))
sys.exit(0 if plan["services"] else 1)
' "$dest/plan.json"; then
    echo "the plan names no backing store, so this would archive no data at all" >&2
    echo "  (are the PODPACK_SERVICE_* markers set? see compose.yaml)" >&2
    rm -rf "$dest"
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. The stores.
#
# A dump, never a copy of the data directory: copying a live store produces a
# torn image that may or may not recover on start, and the failure is silent
# until the day it matters. The command belongs to the service, not to this
# script, and runs inside the service's own container so the credentials come
# from its environment rather than from this process's arguments.
# ---------------------------------------------------------------------------
# Read the whole list before running anything. `podman compose exec` reads
# standard input, and standard input here is the list itself -- so piping the
# services straight into the loop let the first `exec` swallow the rest, and
# the script dumped PostgreSQL, skipped MongoDB, and reported success. Found
# by running it against a site with two stores; it is exactly the silent
# omission this whole feature exists to stop.
dumps=()
while IFS= read -r line; do dumps+=("$line"); done < <(python3 -c '
import json, sys
for service in json.load(open(sys.argv[1]))["services"]:
    if service.get("dump") is None:
        sys.stderr.write("podpack knows no dump command for %s\n" % service["name"])
        raise SystemExit(1)
    print("\t".join([service["name"], service["dump"], service["file"]]))
' "$dest/plan.json")

for entry in "${dumps[@]}"; do
    service="${entry%%$'\t'*}"
    rest="${entry#*$'\t'}"
    dump="${rest%%$'\t'*}"
    file="${rest#*$'\t'}"
    echo "  dumping ${service} ..."
    # </dev/null as well as the array, because a command that unexpectedly
    # reads stdin should get nothing rather than something.
    "${compose[@]}" exec -T "$service" sh -c "$dump" < /dev/null > "${dest}/${file}"
    if [[ ! -s "${dest}/${file}" ]]; then
        echo "the ${service} dump is empty -- refusing to keep a backup that cannot restore" >&2
        rm -rf "$dest"
        exit 1
    fi
    echo "    ${file}  $(wc -c < "${dest}/${file}" | tr -d ' ') bytes"
done

# Every store the plan named must have produced a file. The bug above passed
# every other check in this script: the plan named two services, one dump was
# written, and nothing compared the two numbers.
for expected in $(python3 -c '
import json, sys
print(" ".join(s["file"] for s in json.load(open(sys.argv[1]))["services"]))
' "$dest/plan.json"); do
    if [[ ! -s "${dest}/${expected}" ]]; then
        echo "a store named in the plan produced no dump: ${expected}" >&2
        rm -rf "$dest"
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# 2. Per-app data, as each app describes it.
#
# Shipped data seeds once, on first install, and thereafter the host copy is
# the live one -- editing it takes effect with no rebuild. So this is real
# state that exists nowhere else, not a cache of what is in the package.
#
# Logs are not backed up. They are evidence, not state; a site restored without
# them is whole. Archive them separately if a dispute needs them to survive.
# ---------------------------------------------------------------------------
# A read loop rather than `mapfile`: macOS ships bash 3.2, where mapfile does
# not exist, and the script would die here having already taken a good dump.
tar_args=()
while IFS= read -r line; do tar_args+=("$line"); done < <(python3 -c '
import json, sys
plan = json.load(open(sys.argv[1]))
for app in plan["apps"]:
    if not app["data"]:
        continue
    for name in app["excludes"]:
        print("--exclude=%s/%s" % (app["name"], name))
for app in plan["apps"]:
    if app["data"]:
        print(app["name"])
' "$dest/plan.json")

if [[ ${#tar_args[@]} -eq 0 ]]; then
    echo "  no app keeps data; nothing to archive" > /dev/null
    tar -czf "$dest/app-data.tar.gz" -C "${HOST_DATA_DIR}/apps" --files-from /dev/null
else
    tar -czf "$dest/app-data.tar.gz" -C "${HOST_DATA_DIR}/apps" "${tar_args[@]}"
fi
echo "  app-data.tar.gz  $(wc -c < "$dest/app-data.tar.gz" | tr -d ' ') bytes"

# Anything an app said it keeps outside its own directory. Rare by design, and
# archived separately so that its rarity stays visible in a listing.
extras=()
while IFS= read -r line; do extras+=("$line"); done < <(python3 -c '
import json, sys
for app in json.load(open(sys.argv[1]))["apps"]:
    for path in app["extra"]:
        print(path)
' "$dest/plan.json")
if [[ ${#extras[@]} -gt 0 ]]; then
    tar -czf "$dest/app-extra.tar.gz" -C "${HOST_DATA_DIR}" "${extras[@]}"
    echo "  app-extra.tar.gz $(wc -c < "$dest/app-extra.tar.gz" | tr -d ' ') bytes"
fi

# ---------------------------------------------------------------------------
# 3. Identity and host config.
#
# secrets.env is the file that is in no repository and cannot be
# reconstructed: SECURITY_PASSWORD_SALT keys every stored password hash, and
# SECRET_KEY signs every session and reset link. .env is per-host and would be
# rewritten on a new machine, but is carried anyway because it is the fastest
# way to see how the host that took this was wired.
#
# config/ is in git and is copied all the same: app.toml's app list decides
# which tables the schema is supposed to have, and a restore against a drifted
# app list is the one mismatch nothing downstream catches.
# ---------------------------------------------------------------------------
install -m 600 secrets.env "$dest/secrets.env"
install -m 600 .env "$dest/env"
tar -czf "$dest/config.tar.gz" config
echo "  secrets.env, env, config.tar.gz"

# ---------------------------------------------------------------------------
# 4. Row counts, so a restore can be checked rather than merely observed.
#
# Without these the strongest thing a restore can say is that the tables came
# back -- which a restore of an empty dump also satisfies. Exact counts, not
# pg_stat_user_tables' estimates: this runs on sites small enough that a few
# sequential scans cost nothing, and an estimate that is usually right is worth
# nothing as evidence.
# ---------------------------------------------------------------------------
: > "$dest/rowcounts.txt"
if "${compose[@]}" ps --status running --services 2>/dev/null | grep -qx postgres; then
    while read -r schema table; do
        [[ -n "$table" ]] || continue
        count="$("${compose[@]}" exec -T postgres sh -c \
            "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc 'select count(*) from \"$schema\".\"$table\"'" \
            2>/dev/null | tr -d '\r')"
        printf '%s|%s|%s\n' "$schema" "$table" "$count" >> "$dest/rowcounts.txt"
    done < <("${compose[@]}" exec -T postgres sh -c \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F" " -c "select schemaname, tablename from pg_tables where schemaname not in ('"'"'pg_catalog'"'"', '"'"'information_schema'"'"') order by schemaname, tablename"' \
        2>/dev/null | tr -d '\r')
    echo "  rowcounts.txt    $(wc -l < "$dest/rowcounts.txt" | tr -d ' ') tables"
fi

# ---------------------------------------------------------------------------
# 5. The manifest: what this data means, and what it needs to come back.
# ---------------------------------------------------------------------------
revision="$("${compose[@]}" exec -T postgres sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from app.alembic_version"' \
    2>/dev/null | tr -d '\r' || echo UNKNOWN)"

# Asked of the site rather than of the repository, so it reports the image that
# is actually serving. Framework source is baked into the image, so a checkout
# that moved on without a rebuild leaves the site serving the previous commit;
# when this disagrees with `git commit:` below, the data was produced by the
# build named here, and that is the one to restore onto.
running_build="$(
    curl -s --max-time 5 "http://${WEB_BIND_ADDR:-127.0.0.1}:${WEB_HOST_PORT}/_status" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["build_commit"])' 2>/dev/null \
        || echo "UNKNOWN (not readable when this was taken; /_status wants an admin)"
)"

{
    echo "podpack site backup"
    echo "taken:            ${stamp}"
    echo "from host:        $(hostname)"
    echo "source directory: ${here}"
    echo
    echo "# The three facts that must agree on restore."
    echo "git commit:       $(git rev-parse HEAD 2>/dev/null || echo UNKNOWN)"
    echo "git dirty:        $(test -n "$(git status --porcelain 2>/dev/null)" && echo yes || echo no)"
    echo "alembic revision: ${revision}"
    echo "installed apps:   $(grep -E '^apps *=' config/app.toml || echo UNKNOWN)"
    echo "running build:    ${running_build}"
    echo
    echo "# Environment this was taken from."
    echo "site name:        ${SITE_NAME:-unset}"
    echo "services:         $(python3 -c '
import json, sys
print(", ".join(s["name"] for s in json.load(open(sys.argv[1]))["services"]))
' "$dest/plan.json")"
    echo "data root:        ${HOST_DATA_DIR}"
    echo "log root:         ${HOST_LOG_DIR}"
    echo
    echo "# What each app said about itself; plan.json has it in full."
    python3 -c '
import json, sys
for app in json.load(open(sys.argv[1]))["apps"]:
    said = "declared" if app["declared"] else "did not say"
    kept = "kept" if app["data"] else "SKIPPED, declared stateless"
    print("  %-16s %-12s %s" % (app["name"], said, kept))
' "$dest/plan.json"
    echo
    echo "# Restore with scripts/restore.sh; rehearse with scripts/verify-backup.sh."
} > "$dest/manifest.txt"

# Every secret the site has is in here. Owner-only, said once rather than left
# to whatever umask the operator happened to have.
chmod -R go-rwx "$dest"

echo
echo "backup complete: ${dest}"
du -sh "$dest"
