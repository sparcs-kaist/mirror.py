import sys

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.output import create_output
from prompt_toolkit.shortcuts import print_formatted_text

import mirror.socket.master
from mirror.command.config import _resolve_master_socket


_STDERR = create_output(stdout=sys.stderr)
_SSH_ENV_KEYS = ("SSH_ORIGINAL_COMMAND", "SSH_CONNECTION")


def _emit_error(message: str) -> None:
    """Emit an error line to stderr via prompt_toolkit."""
    print_formatted_text(
        FormattedText([("class:error", f"[ERROR] {message}")]),
        output=_STDERR,
    )


def _emit_ok(message: str) -> None:
    """Emit a success line to stdout via prompt_toolkit."""
    print_formatted_text(FormattedText([("class:success", f"[OK] {message}")]))


def _capture_ssh_env() -> dict[str, str]:
    """Capture SSH-supplied env vars from the current process environment.

    Return:
        ssh_env(dict[str, str]): Subset of os.environ holding the keys archvsync's
            ftpsync consumes when invoked over SSH. Empty if neither is set.
    """
    import os
    return {k: os.environ[k] for k in _SSH_ENV_KEYS if k in os.environ}


def push(pkgid: str, config: str) -> None:
    """Trigger a one-shot push sync of a package via the running master daemon.

    Args:
        pkgid(str): Package ID to sync.
        config(str): Path to the main JSON configuration file (used to resolve socket path).
    """
    socket_path = _resolve_master_socket(None)

    if not mirror.socket.master.is_master_running(socket_path=socket_path):
        _emit_error(f"master daemon is not running; cannot push '{pkgid}'")
        sys.exit(1)

    extra_args = _capture_ssh_env()

    try:
        result = mirror.socket.master.push_sync(pkgid, extra_args=extra_args, socket_path=socket_path)
    except Exception as exc:
        _emit_error(f"push_sync failed: {exc}")
        sys.exit(3)

    status = result.get("status", "unknown")
    _emit_ok(f"push '{pkgid}' -> {status}")
