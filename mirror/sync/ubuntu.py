"""Ubuntu two-stage rsync mirror sync module.

Two entry points:
  - run_standalone: direct subprocess caller for CLI / cron use.
  - execute: daemon entry called by the master scheduler when synctype is "ubuntu".
"""

import logging
import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
import mirror.toolbox
import shlex
import socket
import subprocess
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence


UBUNTU_RSYNC_BASE_ARGS: tuple[str, ...] = (
    "--recursive",
    "--times",
    "--links",
    "--safe-links",
    "--hard-links",
    "--stats",
)

UBUNTU_STAGE1_EXCLUDES: tuple[str, ...] = (
    "Packages*",
    "Sources*",
    "Release*",
    "InRelease",
)

UBUNTU_TRACE_PATH_DEFAULT: str = "project/trace"


def _validate_trace_path(trace_path: str) -> None:
    """Reject trace paths that would escape the destination directory.

    Absolute paths and ".." components let an attacker write the trace file
    outside <dst>, so both are refused.

    Args:
        trace_path(str): Relative subdirectory under <dst>.

    Raises:
        ValueError: If trace_path is absolute or contains ".." components.
    """
    p = Path(trace_path)
    if p.is_absolute():
        raise ValueError(f"trace_path must be relative, got {trace_path!r}")
    if ".." in p.parts:
        raise ValueError(f"trace_path must not contain '..' components, got {trace_path!r}")


def build_ubuntu_commands(
    src: str,
    dst: Path,
    extra_rsync_args: Sequence[str] = (),
    rsync_bin: str = "rsync",
    stage1_excludes: Sequence[str] = UBUNTU_STAGE1_EXCLUDES,
) -> tuple[list[str], list[str]]:
    """Build stage1 and stage2 rsync argv lists for Ubuntu mirroring.

    Args:
        src(str): Rsync source URL or path.
        dst(Path): Local destination directory.
        extra_rsync_args(Sequence[str]): Additional rsync flags appended after base args.
        rsync_bin(str): Path or name of the rsync binary.
        stage1_excludes(Sequence[str]): Patterns to exclude in stage 1.

    Return:
        commands(tuple[list[str], list[str]]): (stage1_argv, stage2_argv).
    """
    src_arg = src if src.endswith("/") else src + "/"
    dst_arg = str(dst) if str(dst).endswith("/") else str(dst) + "/"

    base = [rsync_bin, *UBUNTU_RSYNC_BASE_ARGS, *extra_rsync_args]

    stage1 = list(base)
    for pattern in stage1_excludes:
        stage1.append(f"--exclude={pattern}")
    stage1.append(src_arg)
    stage1.append(dst_arg)

    stage2 = list(base)
    stage2.extend(["--delete", "--delete-after"])
    stage2.append(src_arg)
    stage2.append(dst_arg)

    return stage1, stage2


def build_daemon_shell_command(
    src: str,
    dst: Path,
    trace: bool = True,
    trace_path: str = UBUNTU_TRACE_PATH_DEFAULT,
    trace_hostname: Optional[str] = None,
    extra_rsync_args: Sequence[str] = (),
    rsync_bin: str = "rsync",
    stage1_excludes: Sequence[str] = UBUNTU_STAGE1_EXCLUDES,
) -> str:
    """Build a /bin/dash-runnable shell oneliner for two-stage Ubuntu rsync.

    Quotes every token with shlex.quote so all user-supplied strings (src, dst,
    rsync_bin, extra_rsync_args, stage1_excludes, trace_path) are shell-safe.
    The trace $(hostname -f) subshell is deliberately left unquoted when
    trace_hostname is not supplied, matching upstream Ubuntu mirror script behaviour.

    Args:
        src(str): Rsync source URL or path.
        dst(Path): Local destination directory.
        trace(bool): Whether to append the trace file write command.
        trace_path(str): Relative path under dst where the trace file is stored.
        trace_hostname(Optional[str]): Explicit hostname for the trace file.
            When None or empty, the worker shell expands $(hostname -f) at runtime.
        extra_rsync_args(Sequence[str]): Additional rsync flags.
        rsync_bin(str): Path or name of the rsync binary.
        stage1_excludes(Sequence[str]): Patterns to exclude in stage 1.

    Return:
        oneliner(str): Shell command string suitable for /bin/dash -c.
    """
    stage1, stage2 = build_ubuntu_commands(src, dst, extra_rsync_args, rsync_bin, stage1_excludes)

    def _join(argv: list[str]) -> str:
        return " ".join(shlex.quote(str(t)) for t in argv)

    dst_str = str(dst) if str(dst).endswith("/") else str(dst) + "/"

    parts = [
        "set -e",
        _join(stage1),
        _join(stage2),
    ]

    if trace:
        _validate_trace_path(trace_path)
        trace_dir_quoted = shlex.quote(dst_str + trace_path + "/")
        if trace_hostname:
            # Quote the hostname through shlex.quote and rely on shell
            # juxtaposition (adjacent quoted strings concatenate) so the
            # literal hostname token appears quoted in the oneliner.
            hostname_quoted = shlex.quote(trace_hostname)
            trace_segment = "date -u > " + trace_dir_quoted + hostname_quoted
        else:
            # Leave $(hostname -f) unquoted so the worker shell expands it at runtime
            trace_segment = "date -u > " + trace_dir_quoted + '"$(hostname -f)"'
        parts.append(trace_segment)

    return " && ".join(parts)


def write_trace_file(
    dst: Path,
    trace_path: str = UBUNTU_TRACE_PATH_DEFAULT,
    trace_hostname: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Path:
    """Write a trace file recording the last successful sync time.

    Args:
        dst(Path): Mirror root directory.
        trace_path(str): Relative path under dst for trace files.
        trace_hostname(Optional[str]): Hostname to use as the trace file name.
            Defaults to socket.getfqdn() when None.
        now(Optional[datetime]): Timestamp to record. Defaults to datetime.now(timezone.utc).

    Return:
        trace_file(Path): Path to the written trace file.
    """
    _validate_trace_path(trace_path)
    hostname = trace_hostname or socket.getfqdn()
    trace_dir = dst / trace_path
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_file = trace_dir / hostname
    timestamp = now or datetime.now(timezone.utc)
    # Format matches GNU date -u output: e.g. "Thu May 29 12:34:56 UTC 2026"
    content = timestamp.strftime("%a %b %e %H:%M:%S UTC %Y") + "\n"
    trace_file.write_text(content)
    return trace_file


def run_standalone(
    src: str,
    dst: Path,
    trace: bool = True,
    trace_path: str = UBUNTU_TRACE_PATH_DEFAULT,
    trace_hostname: Optional[str] = None,
    extra_rsync_args: Sequence[str] = (),
    rsync_bin: str = "rsync",
    runner=subprocess.run,
) -> None:
    """Run an Ubuntu two-stage rsync sync directly (standalone, no daemon).

    Args:
        src(str): Rsync source URL or path.
        dst(Path): Local destination directory.
        trace(bool): Whether to write a trace file after a successful sync.
        trace_path(str): Relative path under dst for trace files.
        trace_hostname(Optional[str]): Hostname for the trace file name.
            Defaults to socket.getfqdn() when None.
        extra_rsync_args(Sequence[str]): Additional rsync flags.
        rsync_bin(str): Path or name of the rsync binary.
        runner: Callable matching subprocess.run signature; injectable for tests.

    Return:
        None
    """
    from prompt_toolkit.shortcuts import print_formatted_text
    from prompt_toolkit.formatted_text import FormattedText

    dst = Path(dst)

    if not dst.exists():
        print_formatted_text(FormattedText([("class:warning", f"[WARN] Destination {dst} does not exist; creating it.")]))
        try:
            dst.mkdir(parents=True)
        except OSError as exc:
            print_formatted_text(FormattedText([("class:error", f"[ERROR] Could not create {dst}: {exc}")]))
            sys.exit(1)

    stage1, stage2 = build_ubuntu_commands(src, dst, extra_rsync_args, rsync_bin)

    print_formatted_text(FormattedText([("class:info", f"[INFO] Running stage 1: {' '.join(stage1)}")]))
    result1 = runner(stage1)
    if result1.returncode != 0:
        print_formatted_text(FormattedText([("class:error", f"[ERROR] Stage 1 failed with return code {result1.returncode}")]))
        sys.exit(result1.returncode or 1)

    print_formatted_text(FormattedText([("class:info", f"[INFO] Running stage 2: {' '.join(stage2)}")]))
    result2 = runner(stage2)
    if result2.returncode != 0:
        print_formatted_text(FormattedText([("class:error", f"[ERROR] Stage 2 failed with return code {result2.returncode}")]))
        sys.exit(result2.returncode or 1)

    if trace:
        try:
            trace_file = write_trace_file(dst, trace_path, trace_hostname)
            print_formatted_text(FormattedText([("class:info", f"[INFO] Trace file written: {trace_file}")]))
        except OSError as exc:
            print_formatted_text(FormattedText([("class:error", f"[ERROR] Failed to write trace file: {exc}")]))
            sys.exit(1)


def setup(path: Path, package: "mirror.structure.Package") -> None:
    """Prepare the sync environment (no-op for ubuntu).

    Args:
        path(Path): Unused setup path.
        package(mirror.structure.Package): Package being set up.

    Return:
        None
    """
    pass


def execute(package: "mirror.structure.Package", pkg_logger: logging.Logger) -> None:
    """Run ubuntu two-stage sync for the given package via the worker daemon.

    Args:
        package(mirror.structure.Package): Package to sync.
        pkg_logger(logging.Logger): Logger for this sync session.

    Return:
        None
    """
    pkg_logger.info(f"Starting sync.ubuntu for {package.name}")

    try:
        src = package.settings.src
        dst = Path(package.settings.dst)
        opts = package.settings.options

        trace = bool(opts.get("trace", True))
        trace_path = str(opts.get("trace_path", UBUNTU_TRACE_PATH_DEFAULT))
        extra_rsync_args = list(opts.get("extra_rsync_args", []))
        stage1_excludes = list(opts.get("stage1_excludes", UBUNTU_STAGE1_EXCLUDES))
        rsync_bin = str(opts.get("rsync_bin", "rsync"))
        user = str(opts.get("user", ""))
        password = str(opts.get("password", ""))

        global_hostname = getattr(mirror.conf, "hostname", "") or ""
        trace_hostname = global_hostname if global_hostname else None

        oneliner = build_daemon_shell_command(
            src=src,
            dst=dst,
            trace=trace,
            trace_path=trace_path,
            trace_hostname=trace_hostname,
            extra_rsync_args=extra_rsync_args,
            rsync_bin=rsync_bin,
            stage1_excludes=stage1_excludes,
        )

        env: dict[str, str] = {}
        if user:
            env["USER"] = user
            env["RSYNC_PASSWORD"] = password

        pkg_logger.info(f"+ src={src}")
        pkg_logger.info(f"+ frequency={mirror.toolbox.format_iso_duration(package.syncrate)}")
        pkg_logger.info(f"+ lastupdate={time.ctime(package.lastsync)}")
        pkg_logger.info("Running ubuntu sync (dash -c oneliner)")

        logpath = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                logpath = Path(handler.baseFilename)
                break

        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="ubuntu",
            commandline=["/bin/dash", "-c", oneliner],
            env=env,
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=logpath,
        )

    except AttributeError as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: value not found")
        pkg_logger.error(e)
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)
    except Exception as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def plugin():
    """Entry-point factory for the ubuntu sync plug-in.

    Return:
        record(mirror.plugin.PluginRecord): Sync plug-in record exposing execute.
    """
    from mirror.plugin import sync_plugin
    return sync_plugin(name="ubuntu", execute=execute)
