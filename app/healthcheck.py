"""Container healthcheck: exit 0 only if the app reports itself healthy.

This lives in a file rather than as a `python -c` one-liner in compose.yaml
because podman splits the arguments of a `["CMD", ...]` healthcheck on
whitespace, which mangles any argument containing spaces -- the one-liner
version fails with a SyntaxError and the container is reported unhealthy
however well it is actually running.
"""

import sys
import urllib.request

URL = "http://127.0.0.1:8000/healthz"

try:
    with urllib.request.urlopen(URL, timeout=3) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    # Any failure to reach or read the endpoint is an unhealthy container.
    sys.exit(1)
