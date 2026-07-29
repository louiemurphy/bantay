"""The gym server.

Serves the fixture page, mutated on request according to `?seed=N`. Standard
library only, so `make gym` needs no additional services.

A local fixture is used rather than a public site because the resilience figures
need to be reproducible by a reviewer. A third-party site can be redesigned or
rate-limited at any time, which would make the numbers unverifiable. The suite in
`tests/web/` covers realism against a public site; the gym covers measurement.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .mutations import mutate

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DEFAULT_FIXTURE = "checkout.html"


class MutatingHandler(BaseHTTPRequestHandler):
    """GET /?seed=N[&count=M][&fixture=name.html][&mutations=a,b] -> mutated fixture.

    `mutations` names the operators explicitly and overrides the seeded plan, so
    a test about one specific kind of change can ask for exactly that change
    instead of hoping a seed number happens to produce it.
    """

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path in ("/health", "/healthz"):
            return self._send(200, "text/plain", b"ok")

        fixture = query.get("fixture", [DEFAULT_FIXTURE])[0]
        path = (FIXTURE_DIR / Path(fixture).name)
        if not path.is_file():
            return self._send(404, "text/plain", f"no fixture {fixture!r}".encode())

        html = path.read_text(encoding="utf-8")
        seed_values = query.get("seed", [])
        try:
            seed = int(seed_values[0]) if seed_values else 0
            count = int(query["count"][0]) if "count" in query else None
        except (ValueError, IndexError):
            return self._send(400, "text/plain", b"seed and count must be integers")

        requested = query.get("mutations", [])
        names = [n for n in requested[0].split(",") if n] if requested else None

        applied: list[str] = []
        if seed or names:
            try:
                html, plan = mutate(html, seed, count, names)
            except KeyError as exc:
                return self._send(400, "text/plain", str(exc.args[0]).encode())
            applied = [m.name for m in plan]

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Exposed as headers so a failing run can be diagnosed from logs without
        # re-deriving the plan.
        self.send_header("X-Bantay-Seed", str(seed))
        self.send_header("X-Bantay-Mutations", ",".join(applied) or "none")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Silence per-request logging; the gym report is the output that matters."""


class GymServer:
    """Context-managed background server. Port 0 means the OS picks a free port,
    so parallel gym runs never collide."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._server = ThreadingHTTPServer((host, port), partial(MutatingHandler))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://{self._server.server_address[0]}:{self.port}"

    def url_for(self, seed: int = 0, count: int | None = None) -> str:
        url = f"{self.base_url}/?seed={seed}"
        return f"{url}&count={count}" if count is not None else url

    def start(self) -> "GymServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "GymServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the mutation gym fixture.")
    parser.add_argument("--port", type=int, default=8801)
    args = parser.parse_args()
    server = GymServer(port=args.port).start()
    print(f"gym serving on {server.base_url}  (try {server.url_for(seed=7)})")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
