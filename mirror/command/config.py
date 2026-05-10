import sys
from typing import Optional

import click
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text

import mirror
import mirror.socket.master


def _resolve_master_socket(explicit: Optional[str]) -> str:
    """Resolve the master socket path without invoking ``mirror.config.load``.

    Falls back through: explicit ``--socket`` flag, runtime metadata file
    written by the daemon at startup, then the default socket path. We
    intentionally do NOT read socket_path from config.json because the
    operator may have edited it (which is a non-hot-reloadable change), and
    using the new value would make the CLI miss the running daemon.

    Args:
        explicit(str, optional): Explicit socket path from the ``--socket`` flag.

    Return:
        path(str): Resolved master socket path.
    """
    if explicit:
        return explicit
    runtime_meta = mirror.RUN_PATH / "master.sock.path"
    if runtime_meta.exists():
        try:
            return runtime_meta.read_text().strip()
        except OSError:
            pass
    return str(mirror.socket.master._default_master_socket_path())


@click.group("config")
def config_group() -> None:
    """Configuration management commands."""


@config_group.command("reload")
@click.option(
    "--socket", "socket_path", default=None,
    help="Master socket path (default: read from runtime metadata, then fallback).",
)
@click.option(
    "--timeout", default=30, type=int, show_default=True,
    help="Seconds to wait for the master daemon to apply the reload.",
)
def reload_cmd(socket_path: Optional[str], timeout: int) -> None:
    """Request the running master daemon to reload its config."""
    sock = _resolve_master_socket(socket_path)
    if not mirror.socket.master.is_master_running(socket_path=sock):
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] Master daemon is not running on socket {sock}.")
        ]))
        sys.exit(1)

    try:
        with mirror.socket.master.MasterClient(socket_path=sock) as client:
            result = client.reload(timeout=float(timeout))
    except Exception as exc:
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] Reload RPC failed: {exc}")
        ]))
        sys.exit(1)

    if not isinstance(result, dict) or result.get("status") != "ok":
        err = result.get("error", "unknown") if isinstance(result, dict) else "non-dict response"
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] Reload failed: {err}")
        ]))
        sys.exit(1)

    print_formatted_text(FormattedText([
        ("class:success",
         f"[OK] Reloaded in {result.get('duration_seconds', 0):.2f}s. "
         f"added={result.get('added', [])} removed={result.get('removed', [])} "
         f"modified={result.get('modified', [])}")
    ]))
    for warn in result.get("warnings", []):
        print_formatted_text(FormattedText([("class:warning", f"[WARN] {warn}")]))
    sys.exit(0)
