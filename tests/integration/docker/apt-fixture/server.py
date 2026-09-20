"""HTTP and FTP server for shared signed APT repository fixtures."""

import asyncio
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import aioftp


DATA_ROOT = Path("/srv/data")
DENY_LISTING_MARKER = Path("/srv/data/deny-dists-listing")
PARTIAL_LISTING_MARKER = Path("/srv/data/partial-dists-listing")


class FixtureHandler(SimpleHTTPRequestHandler):
    """Serve repository files and allow tests to deny only dist discovery."""

    def do_GET(self) -> None:
        path = urlsplit(self.path).path.rstrip("/")
        if path == "/debian/dists" and DENY_LISTING_MARKER.exists():
            self.send_error(403, "Distribution listing disabled")
            return
        if path == "/debian/dists" and PARTIAL_LISTING_MARKER.exists():
            body = (
                b'<!doctype html><a href="allonly/">allonly/</a>'
                b'<a href="bookworm/">bookworm/</a>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        """Suppress routine HTTP access logs from the fixture."""
        return None


def serve_http() -> None:
    """Serve all fixture repositories over HTTP."""
    handler = partial(FixtureHandler, directory=DATA_ROOT)
    ThreadingHTTPServer(("", 8000), handler).serve_forever()


async def serve_ftp() -> None:
    """Serve all fixture repositories over anonymous FTP."""
    user = aioftp.User(base_path=DATA_ROOT, home_path="/")
    server = aioftp.Server([user])
    await server.start("0.0.0.0", 2121)
    await server.serve_forever()


if __name__ == "__main__":
    os.chdir(DATA_ROOT)
    threading.Thread(target=serve_http, daemon=True).start()
    asyncio.run(serve_ftp())
