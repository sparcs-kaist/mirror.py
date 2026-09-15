"""HTTP fixture server with an optional denied dists directory listing."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


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


if __name__ == "__main__":
    ThreadingHTTPServer(("", 8000), FixtureHandler).serve_forever()
