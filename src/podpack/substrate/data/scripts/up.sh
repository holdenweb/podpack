#!/bin/bash
# Bring the suite up, always rebuilding, always stamping the commit.
#
# Two mistakes this exists to remove.
#
# Editing framework source under src/ needs a *rebuild*, not a restart: the
# source is baked into the image, so `podman compose restart` cheerfully brings
# back the previous code and the site behaves like the last build. Rebuilding
# unconditionally costs about six seconds when nothing has changed, because
# layers are content-addressed and an untouched file invalidates nothing. That
# is far cheaper than the puzzlement.
#
# And the image records the commit it came from, so /_status can answer "is this
# running the code I am looking at?" exactly rather than by comparing
# timestamps. A `-dirty` suffix means it was built from an uncommitted tree --
# which is normal while working, and worth knowing when it is not.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

# A repository with no commits yet has no HEAD, so both of these fail and the
# stamp used to come out "unknown-dirty" -- which reads as a state rather than
# as the absence of one. A brand-new site hits that on its very first build.
if sha="$(git rev-parse --short HEAD 2>/dev/null)"; then
    git diff --quiet HEAD 2>/dev/null || sha="${sha}-dirty"
else
    sha="unknown"
fi

echo "building from ${sha}"
GIT_SHA="$sha" exec podman compose up -d --build "$@"
