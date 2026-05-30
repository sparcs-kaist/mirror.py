"""CLI group for standalone worker-side sync commands."""

from pathlib import Path
from typing import Optional

import click
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text


@click.group("worker-execute")
def worker_execute_group() -> None:
    """Standalone worker-side syncs that run without the daemon."""


@worker_execute_group.command("ubuntu", no_args_is_help=True)
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


@worker_execute_group.command("jigdo", no_args_is_help=True)
@click.option(
    "--src", required=True, type=str,
    help="rsync source URL or path for the Debian CD jigdo tree.",
)
@click.option(
    "--dst", required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Local destination directory (the data/ root).",
)
@click.option(
    "--jigdo-file", "jigdo_file", required=True, type=str,
    help="Value for the jigdoFile= line in jigdo-mirror.conf (the jigdo-file command + options).",
)
@click.option(
    "--debian-mirror", "debian_mirror", required=True, type=str,
    help="Value for the debianMirror= line (local Debian package mirror, e.g. file:/mirror/ftp/debian).",
)
@click.option(
    "--hostname", default=None, type=str,
    help="Hostname used for AUiP/trace excludes and the trace filename (default: mirror.conf.hostname or socket.getfqdn()).",
)
@click.option(
    "--timeout", default=7200, type=int, show_default=True,
    help="rsync --timeout seconds.",
)
@click.option(
    "--trace/--no-trace", default=True, show_default=True,
    help="Write <dst>/<trace-path>/<hostname> on success.",
)
@click.option(
    "--trace-path", default="project/trace", show_default=True,
    help="Subdirectory under dst for the trace file.",
)
@click.option(
    "--trace-hostname", default=None,
    help="Override the trace filename hostname.",
)
@click.option(
    "--template-exclude", "template_excludes", multiple=True, metavar="PATTERN",
    help="Extra rsync exclude for phase 1 (template sync). Repeatable. Defaults to *.iso when none given.",
)
@click.option(
    "--final-include", "final_includes", multiple=True, metavar="PATTERN",
    help="rsync include pattern for the final ISO pull. Repeatable. Defaults to businesscard/netinst/i386 patterns when none given.",
)
@click.option(
    "--extra-rsync-arg", "extra_rsync_args", multiple=True, metavar="ARG",
    help="Extra arg appended to both rsync phases. Repeatable.",
)
@click.option(
    "--rsync-bin", default="rsync", show_default=True,
    help="rsync executable.",
)
@click.option(
    "--jigdo-mirror-bin", default="jigdo-mirror", show_default=True,
    help="jigdo-mirror executable.",
)
def jigdo_cmd(
    src: str,
    dst: Path,
    jigdo_file: str,
    debian_mirror: str,
    hostname: Optional[str],
    timeout: int,
    trace: bool,
    trace_path: str,
    trace_hostname: Optional[str],
    template_excludes: tuple[str, ...],
    final_includes: tuple[str, ...],
    extra_rsync_args: tuple[str, ...],
    rsync_bin: str,
    jigdo_mirror_bin: str,
) -> None:
    """Debian CD jigdo mirror: rsync templates, regenerate ISOs with jigdo-mirror, then pull a few real ISOs."""
    import mirror.sync.jigdo
    template_excludes = tuple(template_excludes) or mirror.sync.jigdo.JIGDO_TEMPLATE_EXCLUDES
    final_includes = tuple(final_includes) or mirror.sync.jigdo.JIGDO_FINAL_INCLUDES
    mirror.sync.jigdo.run_standalone(
        src=src,
        dst=dst,
        jigdo_file=jigdo_file,
        debian_mirror=debian_mirror,
        hostname=hostname,
        timeout=timeout,
        trace=trace,
        trace_path=trace_path,
        trace_hostname=trace_hostname,
        template_excludes=template_excludes,
        final_includes=final_includes,
        extra_rsync_args=tuple(extra_rsync_args),
        rsync_bin=rsync_bin,
        jigdo_mirror_bin=jigdo_mirror_bin,
    )
