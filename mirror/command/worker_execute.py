"""CLI group for standalone worker-side sync commands."""

from pathlib import Path
from typing import Optional

import click
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text


@click.group("worker-execute")
def worker_execute_group() -> None:
    """Standalone worker-side syncs that run without the daemon."""


@worker_execute_group.command("ubuntu")
@click.option(
    "--src", required=True, type=str,
    help="rsync source URL or path (e.g. rsync://kr.archive.ubuntu.com/ubuntu).",
)
@click.option(
    "--dst", required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Local destination directory.",
)
@click.option(
    "--trace/--no-trace", default=True, show_default=True,
    help="Write <dst>/project/trace/<hostname> on success.",
)
@click.option(
    "--trace-path", default="project/trace", show_default=True,
    help="Subdirectory under dst for the trace file.",
)
@click.option(
    "--trace-hostname", default=None,
    help="Override the hostname used for the trace filename (default: socket.getfqdn()).",
)
@click.option(
    "--extra-rsync-arg", "extra_rsync_args", multiple=True, metavar="ARG",
    help="Extra arg appended to BOTH rsync stages. Repeatable.",
)
@click.option(
    "--rsync-bin", default="rsync", show_default=True,
    help="rsync executable.",
)
def ubuntu_cmd(
    src: str,
    dst: Path,
    trace: bool,
    trace_path: str,
    trace_hostname: Optional[str],
    extra_rsync_args: tuple[str, ...],
    rsync_bin: str,
) -> None:
    """Two-stage Ubuntu archive sync: data first, then metadata + delete."""
    import mirror.sync.ubuntu
    mirror.sync.ubuntu.run_standalone(
        src=src,
        dst=dst,
        trace=trace,
        trace_path=trace_path,
        trace_hostname=trace_hostname,
        extra_rsync_args=tuple(extra_rsync_args),
        rsync_bin=rsync_bin,
    )
