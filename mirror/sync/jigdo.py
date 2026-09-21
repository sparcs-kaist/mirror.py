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

import gzip
import hashlib
import logging
import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
import mirror.toolbox
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence


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

JIGDO_INCLUDE_DEFAULT: str = (
    ".*i386-(CD|DVD)-[1-3].iso.*|"
    ".*amd64-(CD|DVD)-[1-3].iso.*|"
    ".*sparc-(CD|DVD)-[1-3].iso.*|"
    ".*source-(CD|DVD)-[1-3].iso.*"
)

JIGDO_EXCLUDE_DEFAULT: str = ".*kfreebsd.*"

JIGDO_DEFAULT_TIMEOUT: int = 7200

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class _JigdoSet:
    arch: str
    name: str
    jigdo_dir: Path
    image_dir: Path
    images: tuple[Path, ...]


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


def _validate_trace_path(trace_path: str) -> tuple[str, ...]:
    path = Path(trace_path)
    if path.is_absolute() or not path.parts:
        raise ValueError(f"trace_path must be a relative directory, got {trace_path!r}")
    for part in path.parts:
        _validate_name(part)
    return path.parts


def _validate_shell_value(label: str, value: str) -> None:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{label} contains an invalid control character")


def _grep_matches(pattern: str, values: Sequence[str]) -> set[str]:
    """Return values matched by grep's POSIX ERE implementation."""
    _validate_shell_value("regular expression", pattern)
    input_text = "\n".join(values) + ("\n" if values else "")
    result = subprocess.run(
        ["grep", "-E", "--", pattern],
        input=input_text,
        text=True,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
        check=False,
    )
    if result.returncode not in (0, 1):
        message = result.stderr.strip() or "invalid POSIX extended regular expression"
        raise ValueError(message)
    return set(result.stdout.splitlines())


def _select_images(
    names: Sequence[str], include: str, exclude: str
) -> tuple[str, ...]:
    included = _grep_matches(include, names)
    excluded = _grep_matches(exclude, names)
    return tuple(name for name in names if name in included and name not in excluded)


def _safe_relative_path(value: str, *, label: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts:
        raise ValueError(f"{label} must be a relative path, got {value!r}")
    for part in path.parts:
        _validate_name(part)
    return path


def _open_jigdo_text(path: Path):
    with path.open("rb") as stream:
        is_gzip = stream.read(2) == b"\x1f\x8b"
    if is_gzip:
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _jigdo_metadata(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    in_image_section = False
    names: list[str] = []
    templates: list[str] = []
    with _open_jigdo_text(path) as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                in_image_section = line == "[Image]"
                continue
            if in_image_section and line.startswith("Filename="):
                value = line.partition("=")[2].strip()
                relpath = _safe_relative_path(value, label="jigdo image filename")
                names.append(relpath.as_posix())
            if in_image_section and line.startswith("Template="):
                value = line.partition("=")[2].strip()
                relpath = _safe_relative_path(value, label="jigdo template filename")
                templates.append(relpath.as_posix())
    if not names:
        raise ValueError(f"jigdo file has no [Image] Filename: {path}")
    if not templates:
        raise ValueError(f"jigdo file has no [Image] Template: {path}")
    return tuple(names), tuple(templates)


def _discover_jigdo_set(
    dst: Path,
    version: str,
    arch: str,
    set_name: str,
    *,
    include: str,
    exclude: str,
) -> Optional[_JigdoSet]:
    jigdo_dir = _safe_join(dst, version, arch, f"jigdo-{set_name}")
    image_dir = _safe_join(dst, version, arch, f"iso-{set_name}")
    if not jigdo_dir.is_dir():
        raise ValueError(f"jigdo directory is missing: {jigdo_dir}")

    candidates = sorted(jigdo_dir.rglob("*.jigdo"))
    metadata_by_jigdo: list[tuple[Path, tuple[str, ...], tuple[str, ...]]] = []
    all_names: list[str] = []
    for jigdo_path in candidates:
        jigdo_relative = jigdo_path.relative_to(jigdo_dir)
        _safe_join(jigdo_dir, *jigdo_relative.parts)
        if jigdo_path.parent != jigdo_dir:
            raise ValueError(f"nested jigdo files are not supported: {jigdo_path}")
        if jigdo_path.is_symlink() or not jigdo_path.is_file():
            raise ValueError(f"refusing unsafe jigdo file: {jigdo_path}")
        names, templates = _jigdo_metadata(jigdo_path)
        metadata_by_jigdo.append((jigdo_path, names, templates))
        all_names.extend(names)

    filter_names = tuple(f"./{name}" for name in all_names)
    selected_filter_names = _select_images(filter_names, include, exclude)
    selected_names = tuple(name.removeprefix("./") for name in selected_filter_names)
    if not selected_names:
        return None

    selected_set = set(selected_names)
    for jigdo_path, names, templates in metadata_by_jigdo:
        if selected_set.intersection(names):
            for template_name in templates:
                template_relative = _safe_relative_path(
                    template_name, label="jigdo template filename"
                )
                template = _safe_join(
                    jigdo_path.parent, *template_relative.parts
                )
                if template.is_symlink() or not template.is_file():
                    raise ValueError(
                        f"template is missing or unsafe for {jigdo_path}: {template}"
                    )

    images = tuple(
        _safe_join(
            image_dir,
            *_safe_relative_path(name, label="jigdo image filename").parts,
        )
        for name in selected_names
    )
    return _JigdoSet(arch, set_name, jigdo_dir, image_dir, images)


def _read_checksums(image_dir: Path) -> tuple[str, dict[str, str]]:
    if image_dir.is_symlink() or not image_dir.is_dir():
        raise ValueError(f"refusing unsafe image directory: {image_dir}")
    for algorithm, filename in (("sha512", "SHA512SUMS"), ("sha256", "SHA256SUMS")):
        checksum_path = image_dir / filename
        if checksum_path.is_symlink():
            raise ValueError(f"refusing symlinked checksum file: {checksum_path}")
        if not checksum_path.exists():
            continue
        if not checksum_path.is_file():
            raise ValueError(f"checksum path is not a regular file: {checksum_path}")
        checksums: dict[str, str] = {}
        for raw_line in checksum_path.read_text(encoding="utf-8").splitlines():
            fields = raw_line.split(maxsplit=1)
            if len(fields) != 2:
                continue
            digest, raw_name = fields
            name = raw_name.lstrip(" *")
            if name.startswith("./"):
                name = name[2:]
            _safe_relative_path(name, label="checksum filename")
            checksums[name] = digest.lower()
        return algorithm, checksums
    raise RuntimeError(f"no SHA512SUMS or SHA256SUMS in {image_dir}")


def verify_jigdo_images(jigdo_sets: Sequence[_JigdoSet]) -> None:
    for jigdo_set in jigdo_sets:
        algorithm, checksums = _read_checksums(jigdo_set.image_dir)
        for image in jigdo_set.images:
            relative = image.relative_to(jigdo_set.image_dir).as_posix()
            safe_image = _safe_join(
                jigdo_set.image_dir,
                *_safe_relative_path(relative, label="jigdo image filename").parts,
            )
            if safe_image.is_symlink() or not safe_image.is_file():
                raise RuntimeError(f"selected jigdo image was not created: {safe_image}")
            expected = checksums.get(relative)
            if expected is None:
                raise RuntimeError(
                    f"{relative} is missing from checksum file in {jigdo_set.image_dir}"
                )
            digest = hashlib.new(algorithm)
            with safe_image.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise RuntimeError(f"checksum mismatch for {safe_image}")


def write_trace_file(
    dst: Path,
    trace_path: str = JIGDO_TRACE_PATH_DEFAULT,
    trace_hostname: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Path:
    """Atomically write a trace file without following source-provided links."""
    parts = _validate_trace_path(trace_path)
    hostname = trace_hostname or socket.getfqdn()
    _validate_name(hostname)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(dst, flags)
    temporary_name = f".{hostname}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o755, dir_fd=directory_fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd

        try:
            existing = os.stat(hostname, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and stat.S_ISLNK(existing.st_mode):
            raise ValueError(f"refusing symlinked trace file: {hostname}")

        timestamp = now or datetime.now(timezone.utc)
        content = timestamp.strftime("%a %b %e %H:%M:%S UTC %Y") + "\n"
        file_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o644,
            dir_fd=directory_fd,
        )
        try:
            data = content.encode()
            while data:
                data = data[os.write(file_fd, data):]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.replace(
            temporary_name,
            hostname,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    return dst / trace_path / hostname


def _trace_rsync_exclude(
    trace_path: str, trace_hostname: Optional[str], hostname: str
) -> str:
    parts = _validate_trace_path(trace_path)
    filename = trace_hostname or hostname
    _validate_name(filename)
    return f"--exclude=/{'/'.join((*parts, filename))}"


def build_template_rsync_command(
    src: str,
    dst: Path,
    *,
    hostname: str,
    timeout: int = JIGDO_DEFAULT_TIMEOUT,
    trace_path: str = JIGDO_TRACE_PATH_DEFAULT,
    trace_hostname: Optional[str] = None,
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

    cmd: list[str] = [rsync_bin, *JIGDO_RSYNC_BASE_ARGS]
    cmd.extend([
        "--delete",
        "--delete-after",
        f"--timeout={timeout}",
        f"--exclude=Archive-Update-in-Progress-{hostname}",
        _trace_rsync_exclude(trace_path, trace_hostname, hostname),
    ])
    cmd.extend(extra_rsync_args)
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
    trace_path: str = JIGDO_TRACE_PATH_DEFAULT,
    trace_hostname: Optional[str] = None,
    includes: Sequence[str] = JIGDO_FINAL_INCLUDES,
    protected_paths: Sequence[str] = (),
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

    cmd: list[str] = [rsync_bin, *JIGDO_RSYNC_BASE_ARGS]
    cmd.extend([
        "--delete",
        "--delete-after",
        f"--timeout={timeout}",
        "--size-only",
        f"--exclude=Archive-Update-in-Progress-{hostname}",
        _trace_rsync_exclude(trace_path, trace_hostname, hostname),
    ])
    for path in protected_paths:
        relative = _safe_relative_path(path, label="protected image path")
        cmd.append(f"--filter=P /{relative.as_posix()}")
    cmd.extend(extra_rsync_args)
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
    include: str = JIGDO_INCLUDE_DEFAULT,
    exclude: str = JIGDO_EXCLUDE_DEFAULT,
) -> str:
    """Build the jigdo-mirror configuration file content for one arch/set pair.

    Values are shell-quoted because jigdo-mirror sources this file.

    Args:
        jigdo_dir(str): Path to the directory containing .jigdo/.template files.
        image_dir(str): Path to the directory where ISOs will be written.
        tmp_dir(str): Temporary working directory for jigdo-mirror.
        jigdo_file(str): Rsync URL or path to the jigdo-file index.
        debian_mirror(str): URL of the Debian package mirror to fetch pieces from.

    Return:
        conf(str): Shell-safe configuration string ready to write to disk.

    Raises:
        ValueError: If any argument contains a NUL, newline, or carriage return.
    """
    for label, value in (
        ("jigdo_dir", jigdo_dir),
        ("image_dir", image_dir),
        ("tmp_dir", tmp_dir),
        ("jigdo_file", jigdo_file),
        ("debian_mirror", debian_mirror),
        ("include", include),
        ("exclude", exclude),
    ):
        _validate_shell_value(label, value)

    lines = [
        f"jigdoDir={shlex.quote(jigdo_dir)}",
        f"templateDir={shlex.quote(jigdo_dir)}",
        f"imageDir={shlex.quote(image_dir)}",
        f"tmpDir={shlex.quote(tmp_dir)}",
        f"jigdoFile={shlex.quote(jigdo_file)}",
        f"debianMirror={shlex.quote(debian_mirror)}",
        f"include={shlex.quote(include)}",
        f"exclude={shlex.quote(exclude)}",
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
            raise ValueError(f"refusing symlinked version entry: {entry.path}")
        if not entry.is_dir(follow_symlinks=False):
            continue
        arch = entry.name
        try:
            _validate_name(arch)
        except ValueError:
            continue

        buildfile = _safe_join(dst, "project", "build", version, arch)

        if os.path.islink(buildfile):
            raise ValueError(f"refusing symlinked build file: {buildfile}")
        if not buildfile.is_file():
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
    include: str = JIGDO_INCLUDE_DEFAULT,
    exclude: str = JIGDO_EXCLUDE_DEFAULT,
    jigdo_mirror_bin: str = "jigdo-mirror",
    tmp_root: Optional[Path] = None,
    runner=subprocess.run,
    conf_writer=None,
) -> tuple[_JigdoSet, ...]:
    """Regenerate ISO images from downloaded jigdo/template files.

    For each (arch, set_token) pair, builds a jigdo-mirror config file and
    invokes jigdo-mirror to assemble the ISO from locally-cached pieces or
    by fetching from debian_mirror. A private temporary directory is created
    for the run and removed in the finally block.

    Args:
        dst(Path): Mirror root directory.
        version(str): Active version string (e.g. "12.5.0").
        sets(list[tuple[str, str]]): List of (arch, set_token) pairs to process.
        jigdo_file(str): Rsync URL or path to the jigdo-file index.
        debian_mirror(str): URL of the Debian package mirror.
        jigdo_mirror_bin(str): Path or name of the jigdo-mirror binary.
        tmp_root(Optional[Path]): Override for the temporary directory. It must
            not exist and must be contained by dst.
        runner: Callable matching subprocess.run signature; injectable for tests.
        conf_writer: Callable(path, text) to write conf files; injectable for tests.

    Return:
        tuple[_JigdoSet, ...]: Selected sets and their expected image paths.

    Raises:
        ValueError: If a symlinked path component is detected or the image_dir
            final node is a symlink before mkdir.
        RuntimeError: If jigdo-mirror exits with a non-zero return code.
        OSError: If directory creation or conf writing fails.
    """
    dst = dst.absolute()
    selected_sets: list[_JigdoSet] = []
    for arch, set_name in sets:
        jigdo_set = _discover_jigdo_set(
            dst,
            version,
            arch,
            set_name,
            include=include,
            exclude=exclude,
        )
        if jigdo_set is not None:
            selected_sets.append(jigdo_set)
    if not selected_sets:
        raise RuntimeError("no jigdo images matched the include/exclude expressions")

    if conf_writer is None:
        conf_writer = lambda p, text: Path(p).write_text(text, encoding="utf-8")

    if tmp_root is None:
        tmp_root = Path(tempfile.mkdtemp(prefix=".jigdo-", dir=dst))
    else:
        tmp_root = tmp_root.absolute()
        _assert_within(dst, tmp_root)
        if tmp_root.exists() or tmp_root.is_symlink():
            raise ValueError(f"temporary directory already exists: {tmp_root}")
        tmp_root.mkdir(mode=0o700)
    os.chmod(tmp_root, 0o700)
    try:
        for jigdo_set in selected_sets:
            image_dir = jigdo_set.image_dir
            if image_dir.is_symlink():
                raise ValueError(f"refusing to mkdir symlinked image_dir: {image_dir}")
            image_dir.mkdir(parents=True, exist_ok=True)

            set_tmp = tmp_root / f"{jigdo_set.arch}.{jigdo_set.name}"
            conf_path = tmp_root / (
                f"jigdo-mirror.conf.{jigdo_set.arch}.{jigdo_set.name}"
            )

            conf_text = build_jigdo_set_conf(
                str(jigdo_set.jigdo_dir.absolute()),
                str(image_dir.absolute()),
                str(set_tmp.absolute()),
                jigdo_file=jigdo_file,
                debian_mirror=debian_mirror,
                include=include,
                exclude=exclude,
            )
            conf_writer(conf_path, conf_text)

            result = runner(
                [jigdo_mirror_bin, str(conf_path)],
                env={**os.environ, "LC_ALL": "C"},
            )
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError(
                    "jigdo-mirror failed for "
                    f"{jigdo_set.arch}/{jigdo_set.name} (rc={result.returncode})"
                )
        verify_jigdo_images(selected_sets)
        return tuple(selected_sets)
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
    jigdo_include: str = JIGDO_INCLUDE_DEFAULT,
    jigdo_exclude: str = JIGDO_EXCLUDE_DEFAULT,
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

    dst = Path(dst).absolute()

    eff_host = (
        hostname
        or getattr(getattr(mirror, "conf", None), "hostname", "")
        or socket.getfqdn()
    )
    try:
        _validate_name(eff_host)
        if trace:
            _validate_trace_path(trace_path)
            _validate_name(trace_hostname or eff_host)
        _grep_matches(jigdo_include, ())
        _grep_matches(jigdo_exclude, ())
        build_jigdo_set_conf(
            "/jigdo",
            "/image",
            "/tmp",
            jigdo_file=jigdo_file,
            debian_mirror=debian_mirror,
            include=jigdo_include,
            exclude=jigdo_exclude,
        )
        if dst.is_symlink():
            raise ValueError(f"refusing symlinked destination: {dst}")
    except (OSError, ValueError) as exc:
        print_formatted_text(
            FormattedText([("class:error", f"[ERROR] Invalid jigdo settings: {exc}")])
        )
        sys.exit(1)

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

    # Phase 1: template rsync (excludes *.iso)
    cmd1 = build_template_rsync_command(
        src,
        dst,
        hostname=eff_host,
        timeout=timeout,
        trace_path=trace_path,
        trace_hostname=trace_hostname or eff_host,
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
        generated_sets = generate_jigdo_images(
            dst,
            version,
            sets,
            jigdo_file=jigdo_file,
            debian_mirror=debian_mirror,
            include=jigdo_include,
            exclude=jigdo_exclude,
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
        trace_path=trace_path,
        trace_hostname=trace_hostname or eff_host,
        includes=tuple(final_includes),
        protected_paths=tuple(
            image.relative_to(dst).as_posix()
            for jigdo_set in generated_sets
            for image in jigdo_set.images
        ),
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

    try:
        final_version, _ = iter_jigdo_sets(dst)
        if final_version != version:
            raise RuntimeError(
                f"current release changed during sync: {version} -> {final_version}"
            )
        verify_jigdo_images(generated_sets)
    except (OSError, RuntimeError, ValueError) as exc:
        print_formatted_text(
            FormattedText([("class:error", f"[ERROR] Final image verification failed: {exc}")])
        )
        sys.exit(1)

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


def execute(package: "mirror.structure.Package", pkg_logger: logging.Logger, trigger: str = "auto") -> None:
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
        jigdo_include = str(opts.get("jigdo_include", JIGDO_INCLUDE_DEFAULT))
        jigdo_exclude = str(opts.get("jigdo_exclude", JIGDO_EXCLUDE_DEFAULT))
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
            "--jigdo-include", jigdo_include,
            "--jigdo-exclude", jigdo_exclude,
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
