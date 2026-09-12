#!/bin/bash
# Prove a backup can be read, by reading all of it.
#
# A backup regime is a claim about the future, and the only evidence for it is
# a restore that has actually happened. This is the cheap half of that: it
# parses every archive in a backup right through, without applying any of it,
# and says what it found. The site keeps serving throughout and nothing it
# holds is touched.
#
# The expensive half is scripts/restore.sh into a scratch deployment, which is
# what finally proves the data comes back. Do that occasionally; do this
# nightly, straight after the backup, because a truncated archive found now is
# a non-event and the same archive found in six weeks is a disaster.
#
#   scripts/verify-backup.sh              # the most recent backup
#   scripts/verify-backup.sh <directory>  # a named one
#
# Needs the stack up: the tool that reads each dump lives in that store's own
# container, which is also the only place its version is guaranteed to match
# the one that wrote the dump.
#
# Exits non-zero on any failure, so a scheduled run is worth scheduling.
set -euo pipefail

# CDPATH is exported in some shells; with it set, bash echoes the matched
# directory on a `cd`, so the `$(cd ... && pwd)` below would capture the path
# twice and the `cd "$here"` after it would fail on the doubled value. Clear it.
unset CDPATH

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

# shellcheck disable=SC1091
set -a; . ./.env; set +a

compose=(podman compose)
project="${SITE_NAME:-podpack}"
root="${BACKUP_ROOT:-${HOME}/backups/${project}}"

if [[ $# -ge 1 ]]; then
    backup="$1"
else
    backup="${root}/$(ls -1 "$root" 2>/dev/null | sort | tail -1)"
fi

[[ -d "$backup" ]] || { echo "no such backup: ${backup}" >&2; exit 1; }
echo "verifying: ${backup}"

for required in plan.json manifest.txt app-data.tar.gz secrets.env env; do
    if [[ ! -s "${backup}/${required}" ]]; then
        echo "FAILED: ${required} is missing or empty" >&2
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# The stores. Each dump is read by the same tool that would restore it, in the
# same container -- the only check that can fail for the reasons a restore
# would.
# ---------------------------------------------------------------------------
while IFS=$'\t' read -r service file verify; do
    [[ -n "$service" ]] || continue
    if [[ ! -s "${backup}/${file}" ]]; then
        echo "FAILED: ${file} is missing or empty" >&2
        exit 1
    fi
    if ! "${compose[@]}" ps --status running --services 2>/dev/null | grep -qx "$service"; then
        echo "FAILED: ${service} is not running, so its dump cannot be read" >&2
        echo "  bring the stack up; verification needs the store's own tooling" >&2
        exit 1
    fi
    if ! "${compose[@]}" exec -T "$service" sh -c "$verify" < "${backup}/${file}" \
            > "${backup}/.${service}.toc" 2>/dev/null; then
        echo "FAILED: ${service}'s dump could not be read right through" >&2
        echo "  the archive is truncated or corrupt: ${backup}/${file}" >&2
        rm -f "${backup}/.${service}.toc"
        exit 1
    fi
    echo "  ${service}: ${file} reads clean"
done < <(python3 -c '
import json, sys
for service in json.load(open(sys.argv[1]))["services"]:
    print("\t".join([service["name"], service["file"], service["verify"]]))
' "$backup/plan.json")

# An archive can be perfectly valid and hold nothing, which is exactly what a
# dump of the wrong database looks like. PostgreSQL's table of contents says so
# directly.
if [[ -s "${backup}/.postgres.toc" ]]; then
    tables="$(grep -c 'TABLE DATA' "${backup}/.postgres.toc" || true)"
    if [[ "${tables:-0}" -eq 0 ]]; then
        echo "FAILED: the database dump contains no table data at all" >&2
        rm -f "${backup}"/.*.toc
        exit 1
    fi
    echo "  postgres: ${tables} tables carry data"
fi
rm -f "${backup}"/.*.toc

# Recorded so a restore can be *compared*, which is what they are for. Not
# asserted to be non-zero: a site before its first user genuinely has none,
# and podpack's own lab -- which installs no app, so its alembic history has
# never stamped a revision -- reports exactly zero and is entirely correct.
# Failing there would be a tool crying wolf on a legitimate state, which is
# how people learn to stop reading it.
if [[ -s "${backup}/rowcounts.txt" ]]; then
    total="$(awk -F'|' '{sum += $3} END {print sum + 0}' "${backup}/rowcounts.txt")"
    tables="$(wc -l < "${backup}/rowcounts.txt" | tr -d ' ')"
    echo "  rowcounts: ${total} rows across ${tables} tables"
    if [[ "$total" -eq 0 ]]; then
        echo "  NOTE: every table is empty. Right for a site that has no data yet;"
        echo "        alarming for one that had some yesterday. Only you know which."
    fi

    # Against the backup before this one, which is the only reference this tool
    # has for what "normal" looks like here.
    #
    # A restore that aborts leaves a site somebody then brings up anyway, and
    # podpack seeds `pages` from what the apps ship. A backup of *that* is three
    # bytes and one row from the real thing -- measured: 1036570 against
    # 1036573, five rows against four -- and both verify clean. Because it is
    # newer it becomes what `verify-backup.sh` with no argument reaches for, and
    # what anybody asking for "the latest backup" gets.
    #
    # A count going down is the signal. It is a remark and not a failure: rows
    # are deleted legitimately, and a check that cried wolf here is a check
    # people learn to skip. Counts going up say nothing and are not mentioned.
    #
    # Only when this backup sits among the others. A named directory
    # somewhere else has no meaningful neighbour, and guessing one would be
    # worse than saying nothing. String test rather than a resolved path,
    # because `$(cd "$(dirname ...)" && pwd)` fails open when the parent is
    # missing -- the defect this project already has open against backup.sh.
    case "$backup" in
    "${root}/"*)
        previous="$(ls -1 "$root" 2>/dev/null | sort | awk -v cur="$(basename "$backup")" '
            $0 == cur { print prev; exit } { prev = $0 }')"
        ;;
    *) previous="" ;;
    esac

    if [[ -n "$previous" && -s "${root}/${previous}/rowcounts.txt" ]]; then
        fallen="$(awk -F'|' '
            NR == FNR { was[$1 "." $2] = $3; next }
            ($1 "." $2) in was && $3 + 0 < was[$1 "." $2] + 0 {
                printf "        %s.%s  %s -> %s\n", $1, $2, was[$1 "." $2], $3
            }' "${root}/${previous}/rowcounts.txt" "${backup}/rowcounts.txt")"
        gone="$(awk -F'|' '
            NR == FNR { now[$1 "." $2] = 1; next }
            !(($1 "." $2) in now) { printf "        %s.%s  had %s\n", $1, $2, $3
            }' "${backup}/rowcounts.txt" "${root}/${previous}/rowcounts.txt")"

        if [[ -n "$fallen" || -n "$gone" ]]; then
            echo "  NOTE: this backup holds less than ${previous} did."
            [[ -n "$fallen" ]] && { echo "        fewer rows in:"; printf '%s\n' "$fallen"; }
            [[ -n "$gone" ]] && { echo "        tables that have gone entirely:"; printf '%s\n' "$gone"; }
            echo "        Deletions look exactly like this. So does a backup of a site"
            echo "        that was seeded rather than restored. Establish which before"
            echo "        this becomes the copy you reach for."
        else
            echo "  compared with ${previous}: no table has fewer rows"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# The other two legs. A tar that cannot be listed cannot be extracted, and
# secrets.env is the leg whose absence is discovered latest: a restore without
# it produces a site that starts, serves, and cannot verify a single stored
# password.
#
# Whether those values are still the shipped examples is a *configuration*
# question rather than a backup one, and scripts/configure-host.py --check
# answers it properly. Asking it here as well would be a second, worse copy of
# a facility that exists.
# ---------------------------------------------------------------------------
files="$(tar -tzf "${backup}/app-data.tar.gz" | wc -l | tr -d ' ')"
echo "  app data: ${files} entries"

expected="$(python3 -c '
import json, sys
print(sum(1 for app in json.load(open(sys.argv[1]))["apps"] if app["data"]))
' "$backup/plan.json")"
if [[ "$expected" -gt 0 && "$files" -eq 0 ]]; then
    echo "FAILED: ${expected} app(s) keep data, and the archive holds nothing" >&2
    exit 1
fi

if [[ -f "${backup}/app-extra.tar.gz" ]]; then
    tar -tzf "${backup}/app-extra.tar.gz" > /dev/null
    echo "  app extra: reads clean"
fi

echo
echo "VERIFIED: ${backup}"
grep -E '^(taken|from host|source directory|git commit|alembic revision|services):' "${backup}/manifest.txt" | sed 's/^/  /'
