import asyncio
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aioftp


DATA_ROOT = Path("/srv/data")


class QuietHttpHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def serve_http() -> None:
    handler = partial(QuietHttpHandler, directory=DATA_ROOT)
    ThreadingHTTPServer(("", 8001), handler).serve_forever()


async def serve_ftp() -> None:
    user = aioftp.User(base_path=DATA_ROOT, home_path="/")
    server = aioftp.Server([user])
    await server.start("0.0.0.0", 2121)
    await server.serve_forever()


if __name__ == "__main__":
    os.chdir(DATA_ROOT)
    threading.Thread(target=serve_http, daemon=True).start()
    asyncio.run(serve_ftp())
