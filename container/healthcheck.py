"""Container healthcheck: exit 0 only if the app reports itself healthy.

This lives in a file rather than as a `python -c` one-liner in compose.yaml
because podman splits the arguments of a `["CMD", ...]` healthcheck on
whitespace, which mangles any argument containing spaces -- the one-liner
version fails with a SyntaxError and the container is reported unhealthy
however well it is actually running.

It waits for gunicorn before it gives up. podman runs the first check the
moment the container starts, before gunicorn is listening, and the next one a
whole interval later: at 300s, a first check that failed on the refused
connection left `web` reading "starting" for five minutes, though gunicorn had
been listening within a second (holdenweb.com's stack, 2026-11-18). compose's
`start_interval`, which probes faster during start-up instead, is refused by
docker-compose against podman, whose compatible API reports itself older than
the Docker Engine 25 that setting needs.
"""

import sys
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/healthz"

# How long a refused connection is waited out. Only a refusal: nothing is
# listening yet, which is start-up. A 503 or a timeout is an answer, and fails
# at once. compose.yaml's healthcheck `timeout` must stay above this plus one
# request's own 3s, or podman kills the first check while it is still waiting.
READY_WITHIN = 20.0


def check(url: str = URL, ready_within: float = READY_WITHIN) -> int:
    """0 if `url` answers 200, else 1 -- once anything is listening there."""
    deadline = time.monotonic() + ready_within
    while True:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return 0 if response.status == 200 else 1
        except urllib.error.URLError as exc:
            refused = isinstance(exc.reason, ConnectionRefusedError)
            if refused and time.monotonic() < deadline:
                time.sleep(0.5)
                continue
            return 1
        except Exception:
            # Any other failure to reach or read the endpoint is an unhealthy
            # container.
            return 1


if __name__ == "__main__":
    sys.exit(check())
