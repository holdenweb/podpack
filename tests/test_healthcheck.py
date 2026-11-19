"""`container/healthcheck.py`, the check podman runs inside the web container.

podman runs it, podpack never imports it, so it is loaded here from the
packaged substrate by path and its `check()` aimed at a port of the test's own.
What needs pinning is the wait: podman runs the first check the moment the
container starts, before gunicorn is listening, and the next a whole interval
later, so a check that failed on that refusal left `web` reading "starting"
for five minutes. Each test here was watched fail with the wait removed.
"""

import importlib.util
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType

DATA_ROOT = Path(__file__).parents[1] / "src" / "podpack" / "substrate" / "data"


def load() -> ModuleType:
    path = DATA_ROOT / "container" / "healthcheck.py"
    spec = importlib.util.spec_from_file_location("healthcheck", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


healthcheck = load()


def free_port() -> int:
    # Bound and released, so nothing is listening there: the refusal a
    # container's first check meets.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def serve(port: int, status: int) -> HTTPServer:
    class Answer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_server_not_listening_yet_is_waited_for() -> None:
    port = free_port()
    started: list[HTTPServer] = []
    late = threading.Timer(1.0, lambda: started.append(serve(port, 200)))
    late.start()
    try:
        url = f"http://127.0.0.1:{port}/healthz"
        assert healthcheck.check(url, ready_within=10) == 0
    finally:
        late.join()
        for server in started:
            server.shutdown()


def test_a_503_fails_at_once() -> None:
    port = free_port()
    server = serve(port, 503)
    try:
        began = time.monotonic()
        url = f"http://127.0.0.1:{port}/healthz"
        assert healthcheck.check(url, ready_within=10) == 1
        # An answer, however bad, is not start-up: nothing to wait out.
        assert time.monotonic() - began < 1
    finally:
        server.shutdown()


def test_a_server_that_never_listens_fails_once_the_wait_is_over() -> None:
    port = free_port()
    began = time.monotonic()
    assert healthcheck.check(f"http://127.0.0.1:{port}/healthz", ready_within=1) == 1
    assert 1 <= time.monotonic() - began < 3


def test_compose_gives_the_first_check_time_to_wait() -> None:
    # podman kills a check at compose's `timeout`. Shorter than the wait plus
    # one request's own 3s, and the first check dies mid-wait, so `web` reads
    # "starting" for an interval again -- with nothing to say why.
    text = (DATA_ROOT / "compose.yaml").read_text()
    block = text.split('"/app/healthcheck.py"]', 1)[1]
    match = re.search(r"^\s+timeout:\s*(\d+)s\s*$", block, re.M)
    assert match, "no timeout after web's healthcheck test in compose.yaml"
    assert int(match.group(1)) > healthcheck.READY_WITHIN + 3
