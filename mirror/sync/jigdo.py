"""Three-phase Debian CD jigdo mirror sync module.

Phase 1: template rsync — syncs all jigdo/template files from the remote,
    excluding *.iso, into the local destination.
Phase 2: local ISO regeneration — uses jigdo-mirror to reconstruct ISO images
    from the downloaded .jigdo and .template files, fetching missing pieces
    from a Debian mirror.
Phase 3: final size-only rsync — syncs a small set of real ISOs (businesscard,
    netinst, i386 catch-all) using --size-only to avoid re-downloading large
    files that already exist.
Phase 4: trace file — writes <dst>/project/trace/<host> with the current UTC
    date, matching the format produced by `date -u`.

Two entry points:
  - run_standalone: direct subprocess caller for CLI / cron use.
  - execute: daemon entry called by the master scheduler when synctype is "jigdo".
"""

import logging
import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
import mirror.toolbox
import os
import re
import shutil
import socket
import subprocess
import sys
import time

from pathlib import Path
from typing import Optional, Sequence

from mirror.sync.ubuntu import write_trace_file, _validate_trace_path


JIGDO_RSYNC_BASE_ARGS: tuple[str, ...] = (
    "--recursive",
    "--times",
    "--links",
    "--hard-links",
    "--stats",
)

JIGDO_TEMPLATE_EXCLUDES: tuple[str, ...] = ("*.iso",)

JIGDO_FINAL_INCLUDES: tuple[str, ...] = (
    "*businesscard*.iso",
    "*netinst*.iso",
    "i386/**.iso",
)

JIGDO_TRACE_PATH_DEFAULT: str = "project/trace"

JIGDO_TMP_DIRNAME: str = ".~tmp~"

JIGDO_DEFAULT_TIMEOUT: int = 7200

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_name(name: str) -> None:
    """Reject names that are empty, ".", "..", or contain unsafe characters."""
    if not name:
        raise ValueError("name must not be empty")
    if name == "." or name == "..":
        raise ValueError(f"name must not be '.' or '..', got {name!r}")
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"name contains unsafe characters: {name!r}. "
            "Only [A-Za-z0-9][A-Za-z0-9._-]* is allowed."
        )


def _assert_within(root: Path, path: Path) -> None:
    """Assert that path is contained within root (resolved).

    Args:
        root(Path): The allowed root directory.
        path(Path): The path to validate.

    Raises:
        ValueError: If the resolved path is not under or equal to the resolved root.
    """
    resolved = path.resolve()
    rootr = root.resolve()
    if resolved != rootr and rootr not in resolved.parents:
        raise ValueError(
            f"path {path!r} resolves to {resolved!r} which is outside root {rootr!r}"
        )


def _safe_join(root: Path, *parts: str, validate: bool = True) -> Path:
    """Join path components under root, rejecting symlinked components and escapes.

    Builds the path incrementally. At each step, if validate is True, the
    component is checked by _validate_name (rejects "/", spaces, shell metachars,
    control chars including \\n/\\r). After joining, any symlinked intermediate
    component is rejected to prevent directory-traversal via symlinks. Finally,
    _assert_within ensures the result remains inside root.

    Rejects symlinked components on BOTH read and write paths and guarantees
    containment under root.

    Args:
        root(Path): The root directory that the result must stay within.
        *parts(str): Path components to join under root.
        validate(bool): Whether to run _validate_name on each component.

    Return:
        path(Path): The safely-joined path.

    Raises:
        ValueError: If any component fails validation, any intermediate is a
            symlink, or the final path escapes root.
    """
    current = root
    for part in parts:
        if validate:
            _validate_name(part)
        current = current / part
        if os.path.islink(current):
            raise ValueError(f"refusing symlinked path component: {current}")
        _assert_within(root, current)
    return current


def build_template_rsync_command(
    src: str,
    dst: Path,
    *,
    hostname: str,
    timeout: int = JIGDO_DEFAULT_TIMEOUT,
    extra_rsync_args: Sequence[str] = (),
    rsync_bin: str = "rsync",
    excludes: Sequence[str] = JIGDO_TEMPLATE_EXCLUDES,
) -> list[str]:
    """Build the rsync argv list for Phase 1 (template sync, excluding *.iso).

    Args:
        src(str): Rsync source URL or path.
        dst(Path): Local destination directory.
        hostname(str): Local mirror hostname (used for exclude patterns).
        timeout(int): Rsync --timeout value in seconds.
        extra_rsync_args(Sequence[str]): Additional rsync flags appended after base args.
        rsync_bin(str): Path or name of the rsync binary.
        excludes(Sequence[str]): Patterns to exclude (default: JIGDO_TEMPLATE_EXCLUDES).

    Return:
        argv(list[str]): rsync argument list suitable for subprocess.
    """
    src_arg = src if src.endswith("/") else src + "/"
    dst_arg = str(dst) if str(dst).endswith("/") else str(dst) + "/"

    cmd: list[str] = [rsync_bin, *JIGDO_RSYNC_BASE_ARGS, *extra_rsync_args]
    cmd.extend([
        "--delete",
        "--delete-after",
        f"--timeout={timeout}",
        f"--exclude=Archive-Update-in-Progress-{hostname}",
        f"--exclude=project/trace/{hostname}",
    ])
    for pat in excludes:
        cmd.append(f"--exclude={pat}")
    cmd.append(src_arg)
    cmd.append(dst_arg)
    return cmd


def build_final_rsync_command(
    src: str,
    dst: Path,
    *,
    hostname: str,
    timeout: int = JIGDO_DEFAULT_TIMEOUT,
    includes: Sequence[str] = JIGDO_FINAL_INCLUDES,
    extra_rsync_args: Sequence[str] = (),
    rsync_bin: str = "rsync",
) -> list[str]:
    """Build the rsync argv list for Phase 3 (size-only sync of final ISOs).

    Order is load-bearing for rsync first-match-wins semantics:
    AUiP/trace excludes come first, then the include patterns, then the
    trailing --exclude=*.iso to block everything else.

    Args:
        src(str): Rsync source URL or path.
        dst(Path): Local destination directory.
        hostname(str): Local mirror hostname (used for exclude patterns).
        timeout(int): Rsync --timeout value in seconds.
        includes(Sequence[str]): Patterns to include (default: JIGDO_FINAL_INCLUDES).
        extra_rsync_args(Sequence[str]): Additional rsync flags appended after base args.
        rsync_bin(str): Path or name of the rsync binary.

    Return:
        argv(list[str]): rsync argument list suitable for subprocess.
    """
    src_arg = src if src.endswith("/") else src + "/"
    dst_arg = str(dst) if str(dst).endswith("/") else str(dst) + "/"

    cmd: list[str] = [rsync_bin, *JIGDO_RSYNC_BASE_ARGS, *extra_rsync_args]
    cmd.extend([
        "--delete",
        "--delete-after",
        f"--timeout={timeout}",
        "--size-only",
        f"--exclude=Archive-Update-in-Progress-{hostname}",
        f"--exclude=project/trace/{hostname}",
    ])
    for pat in includes:
        cmd.append(f"--include={pat}")
    cmd.append("--exclude=*.iso")
    cmd.append(src_arg)
    cmd.append(dst_arg)
    return cmd


def build_jigdo_set_conf(
    jigdo_dir: str,
    image_dir: str,
    tmp_dir: str,
    *,
    jigdo_file: str,
    debian_mirror: str,
) -> str:
    """Build the jigdo-mirror configuration file content for one arch/set pair.

    jigdo-mirror parses the config line-by-line, so newline injection is the
    real threat; any \\n or \\r in any argument is rejected. double-quote
    characters in jigdo_file and debian_mirror are also rejected because those
    values appear inside double-quotes in the output.

    Args:
        jigdo_dir(str): Path to the directory containing .jigdo/.template files.
        image_dir(str): Path to the directory where ISOs will be written.
        tmp_dir(str): Temporary working directory for jigdo-mirror.
        jigdo_file(str): Rsync URL or path to the jigdo-file index.
        debian_mirror(str): URL of the Debian package mirror to fetch pieces from.

    Return:
        conf(str): Six-line configuration string ready to write to disk.

    Raises:
        ValueError: If any argument contains \\n or \\r, or if jigdo_file or
            debian_mirror contains a double-quote character.
    """
    for label, value in (
        ("jigdo_dir", jigdo_dir),
        ("image_dir", image_dir),
        ("tmp_dir", tmp_dir),
        ("jigdo_file", jigdo_file),
        ("debian_mirror", debian_mirror),
    ):
        if "\n" in value or "\r" in value:
            raise ValueError(
                f"{label} must not contain newline characters, got {value!r}"
            )
    for label, value in (("jigdo_file", jigdo_file), ("debian_mirror", debian_mirror)):
        if '"' in value:
            raise ValueError(
                f'{label} must not contain double-quote characters, got {value!r}'
            )

    lines = [
        f"jigdoDir={jigdo_dir}",
        f"templateDir={jigdo_dir}",
        f"imageDir={image_dir}",
        f"tmpDir={tmp_dir}",
        f'jigdoFile="{jigdo_file}"',
        f'debianMirror="{debian_mirror}"',
    ]
    return "\n".join(lines) + "\n"


def iter_jigdo_sets(dst: Path) -> tuple[str, list[tuple[str, str]]]:
    """Walk the destination tree to discover version, architectures, and set names.

    Reads the 'current' symlink to determine the active version, then scans
    <dst>/<version>/ for architecture directories. For each arch, reads the
    corresponding build file at <dst>/project/build/<version>/<arch> to get
    the list of set tokens (e.g. "bd", "dvd").

    Args:
        dst(Path): Mirror root directory.

    Return:
        result(tuple[str, list[tuple[str, str]]]): (version, sets) where
            version is the string pointed to by the 'current' symlink and
            sets is a list of (arch, set_token) pairs sorted by arch then token.

    Raises:
        ValueError: If 'current' is missing or not a symlink, the symlink
            target fails _validate_name, the version directory does not exist,
            or a symlinked component is detected during path traversal.
    """
    link = dst / "current"
    if not os.path.islink(link):
        raise ValueError("data/current is missing or not a symlink")

    target = os.readlink(link)
    _validate_name(target)
    version = target

    version_dir = _safe_join(dst, version)
    if not version_dir.is_dir():
        raise ValueError(f"version dir is not a directory: {version_dir}")

    sets: list[tuple[str, str]] = []

    with os.scandir(version_dir) as it:
        entries = sorted(it, key=lambda e: e.name)

    for entry in entries:
        if entry.is_symlink():
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue
        arch = entry.name
        try:
            _validate_name(arch)
        except ValueError:
            continue

        try:
            buildfile = _safe_join(dst, "project", "build", version, arch)
        except ValueError:
            continue

        if os.path.islink(buildfile) or not buildfile.is_file():
            continue

        content = buildfile.read_text()
        for token in content.split():
            _validate_name(token)
            sets.append((arch, token))

    return version, sets


def generate_jigdo_images(
    dst: Path,
    version: str,
    sets: list[tuple[str, str]],
    *,
    jigdo_file: str,
    debian_mirror: str,
    jigdo_mirror_bin: str = "jigdo-mirror",
    tmp_root: Optional[Path] = None,
    runner=subprocess.run,
    conf_writer=None,
) -> None:
    """Regenerate ISO images from downloaded jigdo/template files.

    For each (arch, set_token) pair, builds a jigdo-mirror config file and
    invokes jigdo-mirror to assemble the ISO from locally-cached pieces or
    by fetching from debian_mirror. A shared tmp_root directory is created
    before the loop and unconditionally removed in the finally block.

    Args:
        dst(Path): Mirror root directory.
        version(str): Active version string (e.g. "12.5.0").
        sets(list[tuple[str, str]]): List of (arch, set_token) pairs to process.
        jigdo_file(str): Rsync URL or path to the jigdo-file index.
        debian_mirror(str): URL of the Debian package mirror.
        jigdo_mirror_bin(str): Path or name of the jigdo-mirror binary.
        tmp_root(Optional[Path]): Override for the temporary directory. Defaults
            to <dst>/.~tmp~.
        runner: Callable matching subprocess.run signature; injectable for tests.
        conf_writer: Callable(path, text) to write conf files; injectable for tests.

    Return:
        None

    Raises:
        ValueError: If a symlinked path component is detected or the image_dir
            final node is a symlink before mkdir.
        RuntimeError: If jigdo-mirror exits with a non-zero return code.
        OSError: If directory creation or conf writing fails.
    """
    if conf_writer is None:
        conf_writer = lambda p, text: Path(p).write_text(text)

    if tmp_root is None:
        tmp_root = _safe_join(dst, JIGDO_TMP_DIRNAME, validate=False)

    try:
        if os.path.islink(tmp_root):
            raise ValueError(f"refusing to use symlinked tmp_root: {tmp_root}")
        shutil.rmtree(tmp_root, ignore_errors=True)
        tmp_root.mkdir(parents=True, exist_ok=True)
        os.chmod(tmp_root, 0o700)

        for arch, s in sets:
            jigdo_dir = _safe_join(dst, version, arch, f"jigdo-{s}")
            image_dir = _safe_join(dst, version, arch, f"iso-{s}")

            if os.path.islink(image_dir):
                raise ValueError(f"refusing to mkdir symlinked image_dir: {image_dir}")
            image_dir.mkdir(parents=True, exist_ok=True)

            set_tmp = tmp_root / f"{arch}.{s}"
            conf_path = tmp_root / f"jigdo-mirror.conf.{arch}.{s}"

            conf_text = build_jigdo_set_conf(
                str(jigdo_dir),
                str(image_dir),
                str(set_tmp),
                jigdo_file=jigdo_file,
                debian_mirror=debian_mirror,
            )
            conf_writer(conf_path, conf_text)

            result = runner([jigdo_mirror_bin, str(conf_path)])
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError(
                    f"jigdo-mirror failed for {arch}/{s} (rc={result.returncode})"
                )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def run_standalone(
    src: str,
    dst: Path,
    *,
    jigdo_file: str,
    debian_mirror: str,
    hostname: Optional[str] = None,
    timeout: int = JIGDO_DEFAULT_TIMEOUT,
    trace: bool = True,
    trace_path: str = JIGDO_TRACE_PATH_DEFAULT,
    trace_hostname: Optional[str] = None,
    template_excludes: Sequence[str] = JIGDO_TEMPLATE_EXCLUDES,
    final_includes: Sequence[str] = JIGDO_FINAL_INCLUDES,
    extra_rsync_args: Sequence[str] = (),
    rsync_bin: str = "rsync",
    jigdo_mirror_bin: str = "jigdo-mirror",
    runner=subprocess.run,
) -> None:
    """Run a three-phase jigdo CD mirror sync directly (standalone, no daemon).

    Args:
        src(str): Rsync source URL or path.
        dst(Path): Local destination directory.
        jigdo_file(str): Rsync URL or path to the jigdo-file index.
        debian_mirror(str): URL of the Debian package mirror for ISO assembly.
        hostname(Optional[str]): Override for the local hostname in exclude patterns.
            Defaults to mirror.conf.hostname or socket.getfqdn().
        timeout(int): Rsync --timeout value in seconds.
        trace(bool): Whether to write a trace file after a successful sync.
        trace_path(str): Relative path under dst for trace files.
        trace_hostname(Optional[str]): Hostname for the trace file name.
            Defaults to eff_host when None.
        template_excludes(Sequence[str]): Extra exclude patterns for Phase 1.
        final_includes(Sequence[str]): Include patterns for Phase 3.
        extra_rsync_args(Sequence[str]): Additional rsync flags for both rsync phases.
        rsync_bin(str): Path or name of the rsync binary.
        jigdo_mirror_bin(str): Path or name of the jigdo-mirror binary.
        runner: Callable matching subprocess.run signature; injectable for tests.

    Return:
        None
    """
    from prompt_toolkit.shortcuts import print_formatted_text
    from prompt_toolkit.formatted_text import FormattedText

    dst = Path(dst)

    if not dst.exists():
        print_formatted_text(
            FormattedText([
                ("class:warning", f"[WARN] Destination {dst} does not exist; creating it.")
            ])
        )
        try:
            dst.mkdir(parents=True)
        except OSError as exc:
            print_formatted_text(
                FormattedText([("class:error", f"[ERROR] Could not create {dst}: {exc}")])
            )
            sys.exit(1)

    eff_host = (
        hostname
        or getattr(getattr(mirror, "conf", None), "hostname", "")
        or socket.getfqdn()
    )

    # Phase 1: template rsync (excludes *.iso)
    cmd1 = build_template_rsync_command(
        src,
        dst,
        hostname=eff_host,
        timeout=timeout,
        extra_rsync_args=tuple(extra_rsync_args),
        rsync_bin=rsync_bin,
        excludes=tuple(template_excludes),
    )
    print_formatted_text(
        FormattedText([("class:info", f"[INFO] Phase 1 (template rsync): {' '.join(cmd1)}")])
    )
    r1 = runner(cmd1)
    if r1.returncode != 0:
        print_formatted_text(
            FormattedText([
                ("class:error", f"[ERROR] Phase 1 failed with return code {r1.returncode}")
            ])
        )
        sys.exit(r1.returncode or 1)

    # Phase 2: local ISO regeneration via jigdo-mirror
    try:
        version, sets = iter_jigdo_sets(dst)
    except ValueError as exc:
        print_formatted_text(
            FormattedText([("class:error", f"[ERROR] Failed to read jigdo set list: {exc}")])
        )
        sys.exit(1)

    try:
        generate_jigdo_images(
            dst,
            version,
            sets,
            jigdo_file=jigdo_file,
            debian_mirror=debian_mirror,
            jigdo_mirror_bin=jigdo_mirror_bin,
            runner=runner,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print_formatted_text(
            FormattedText([("class:error", f"[ERROR] Phase 2 (jigdo-mirror) failed: {exc}")])
        )
        sys.exit(1)

    # Phase 3: final size-only rsync of selected ISOs
    cmd3 = build_final_rsync_command(
        src,
        dst,
        hostname=eff_host,
        timeout=timeout,
        includes=tuple(final_includes),
        extra_rsync_args=tuple(extra_rsync_args),
        rsync_bin=rsync_bin,
    )
    print_formatted_text(
        FormattedText([("class:info", f"[INFO] Phase 3 (final rsync): {' '.join(cmd3)}")])
    )
    r3 = runner(cmd3)
    if r3.returncode != 0:
        print_formatted_text(
            FormattedText([
                ("class:error", f"[ERROR] Phase 3 failed with return code {r3.returncode}")
            ])
        )
        sys.exit(r3.returncode or 1)

    # Phase 4: trace file
    if trace:
        try:
            tf = write_trace_file(dst, trace_path, trace_hostname or eff_host)
            print_formatted_text(
                FormattedText([("class:info", f"[INFO] Trace file written: {tf}")])
            )
        except (OSError, ValueError) as exc:
            print_formatted_text(
                FormattedText([
                    ("class:error", f"[ERROR] Failed to write trace file: {exc}")
                ])
            )
            sys.exit(1)


def setup(path: Path, package: "mirror.structure.Package") -> None:
    """Prepare the sync environment (no-op for jigdo).

    Args:
        path(Path): Unused setup path.
        package(mirror.structure.Package): Package being set up.

    Return:
        None
    """
    pass


def execute(package: "mirror.structure.Package", pkg_logger: logging.Logger) -> None:
    """Run jigdo three-phase sync for the given package via the worker daemon.

    Args:
        package(mirror.structure.Package): Package to sync.
        pkg_logger(logging.Logger): Logger for this sync session.

    Return:
        None
    """
    pkg_logger.info(f"Starting sync.jigdo for {package.name}")

    try:
        src = package.settings.src
        dst = Path(package.settings.dst)
        opts = package.settings.options

        # Required options — KeyError if missing
        jigdo_file = str(opts["jigdo_file"])
        debian_mirror = str(opts["debian_mirror"])

        user = str(opts.get("user", ""))
        password = str(opts.get("password", ""))
        timeout = int(opts.get("timeout", JIGDO_DEFAULT_TIMEOUT))
        trace = bool(opts.get("trace", True))
        trace_path = str(opts.get("trace_path", JIGDO_TRACE_PATH_DEFAULT))
        template_excludes = list(opts.get("template_excludes", JIGDO_TEMPLATE_EXCLUDES))
        final_includes = list(opts.get("final_includes", JIGDO_FINAL_INCLUDES))
        extra_rsync_args = list(opts.get("extra_rsync_args", []))
        rsync_bin = str(opts.get("rsync_bin", "rsync"))
        jigdo_mirror_bin = str(opts.get("jigdo_mirror_bin", "jigdo-mirror"))

        opt_host = str(opts.get("hostname", ""))
        eff_host = (
            opt_host
            or getattr(getattr(mirror, "conf", None), "hostname", "")
            or socket.getfqdn()
        )

        argv: list[str] = [
            sys.executable, "-m", "mirror", "worker-execute", "jigdo",
            "--src", src,
            "--dst", str(dst),
            "--jigdo-file", jigdo_file,
            "--debian-mirror", debian_mirror,
            "--hostname", eff_host,
            "--timeout", str(timeout),
            "--trace-path", trace_path,
            "--rsync-bin", rsync_bin,
            "--jigdo-mirror-bin", jigdo_mirror_bin,
        ]
        if not trace:
            argv.append("--no-trace")
        for pat in template_excludes:
            argv += ["--template-exclude", str(pat)]
        for pat in final_includes:
            argv += ["--final-include", str(pat)]
        for a in extra_rsync_args:
            argv += ["--extra-rsync-arg", str(a)]

        env: dict[str, str] = {}
        if user:
            env["USER"] = user
            env["RSYNC_PASSWORD"] = password

        pkg_logger.info(f"+ src={src}")
        pkg_logger.info(
            f"+ frequency={mirror.toolbox.format_iso_duration(package.syncrate)}"
        )
        pkg_logger.info(f"+ lastupdate={time.ctime(package.lastsync)}")
        pkg_logger.info("Running jigdo sync (delegated to worker-execute jigdo)")

        logpath = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                logpath = Path(handler.baseFilename)
                break

        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="jigdo",
            commandline=argv,
            env=env,
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=logpath,
        )

    except KeyError as e:
        pkg_logger.error(
            f"Sync for {package.pkgid} failed: missing required option {e}"
        )
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)
    except AttributeError as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: value not found")
        pkg_logger.error(e)
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)
    except Exception as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def plugin() -> "mirror.plugin.PluginRecord":
    """Entry-point factory for the jigdo sync plug-in.

    Return:
        record(mirror.plugin.PluginRecord): Sync plug-in record exposing execute.
    """
    from mirror.plugin import sync_plugin
    return sync_plugin(name="jigdo", execute=execute)
