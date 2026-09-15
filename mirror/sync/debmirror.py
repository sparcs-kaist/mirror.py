"""CLI executor for the Debian `debmirror` tool.

Missing dist, section, or arch selections are resolved from repository metadata
inside the worker. This module runs debmirror with an isolated temporary config
and cleans it up when the child exits. Command building stays pure.

Debmirror normally auto-reads /etc/debmirror.conf and ~/.debmirror.conf.
The worker supplies an empty temporary config file so package settings remain
authoritative. Other process environment, such as proxy and GNUPGHOME
variables, is intentionally inherited by the worker.
"""

from __future__ import annotations

import ftplib
import hashlib
import html.parser
import logging
import os
import posixpath
import re
import resource
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


import mirror
import mirror.socket.worker
import mirror.structure
import mirror.sync
import mirror.toolbox

_METHOD_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.@:_-]*$")
_ROOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/~-]*$")
_DIST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]*$")
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RSYNC_EXTRA_TOKEN_RE = re.compile(r"^[a-z]+$")

_METHODS = {"ftp", "http", "https", "rsync", "file"}
_CLEANUP_MODES = {"postcleanup", "precleanup", "nocleanup"}
_DIFF_MODES = {"use", "mirror", "none"}
_RSYNC_EXTRA_CHOICES = {"doc", "indices", "tools", "trace", "none"}
_MALFORMED_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def setup(path: Path, package: mirror.structure.Package) -> None:
    """Prepare the sync environment (no-op for debmirror)."""
    pass


def _no_ctrl(value: str, label: str) -> str:
    """Reject control characters (newline, carriage return, ord < 32, DEL)."""
    for ch in value:
        if ch in ("\n", "\r") or ord(ch) < 32 or ord(ch) == 127:
            raise ValueError(f"debmirror {label}: must not contain control characters")
    return value


def _validate_token(value: str, label: str, pattern: "re.Pattern[str]") -> str:
    """Validate a single flag-value token: str, no control chars, non-empty, no leading '-', matches charset."""
    if not isinstance(value, str):
        raise ValueError(f"debmirror {label}: must be a string, got {type(value)!r}")
    value = _no_ctrl(value, label)
    if not value:
        raise ValueError(f"debmirror {label}: must not be empty")
    if value.startswith("-"):
        raise ValueError(f"debmirror {label}: must not start with '-' (looks like a flag): {value!r}")
    if not pattern.fullmatch(value):
        raise ValueError(f"debmirror {label}: contains disallowed characters: {value!r}")
    return value


def _join_multi(value, label: str, pattern: "re.Pattern[str]") -> str:
    """Validate a str (optionally comma-separated) or list[str] as tokens; return a comma-joined string."""
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(f"debmirror {label}: must be a string or list of strings")
    if not items:
        raise ValueError(f"debmirror {label}: must not be empty")
    return ",".join(_validate_token(item, label, pattern) for item in items)


def _validate_enum(value: str, allowed: set, label: str) -> str:
    """Validate that value is a string member of the allowed set."""
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"debmirror {label}: must be one of {sorted(allowed)}, got {value!r}")
    return value


def _validate_keyring(path: str) -> str:
    """Validate a keyring path: str, no control chars, absolute.

    Pure: does not touch the filesystem. File existence is checked (and warned
    about) in execute(), which already performs I/O.
    """
    if not isinstance(path, str):
        raise ValueError(f"debmirror keyring: must be a string, got {type(path)!r}")
    path = _no_ctrl(path, "keyring")
    if not path:
        raise ValueError("debmirror keyring: must not be empty")
    if not Path(path).is_absolute():
        raise ValueError(f"debmirror keyring: must be an absolute path, got {path!r}")
    return path


def _validate_dst(dst: str) -> str:
    """Validate the mirrordir positional: str, no control chars, absolute path."""
    if not isinstance(dst, str):
        raise ValueError(f"debmirror dst: must be a string, got {type(dst)!r}")
    dst = _no_ctrl(dst, "dst")
    if not dst or not Path(dst).is_absolute():
        raise ValueError(f"debmirror dst: must be a non-empty absolute path, got {dst!r}")
    return dst


def _extend_filter_args(argv: list, opts: dict, key: str, flag: str) -> None:
    """Append a repeatable filter flag (exclude/include/etc.) for each item in a list option."""
    values = opts.get(key)
    if not values:
        return
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        raise ValueError(f"debmirror {key}: must be a string or list of strings")
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"debmirror {key}: each item must be a string")
        argv.extend([flag, _no_ctrl(item, key)])


def _validate_file_root(root: str) -> str:
    """Validate an absolute local archive root for debmirror's file method."""
    if not isinstance(root, str):
        raise ValueError(f"debmirror root: must be a string, got {type(root)!r}")
    root = _no_ctrl(root, "root")
    if not root or not Path(root).is_absolute():
        raise ValueError(f"debmirror root: file method requires an absolute path, got {root!r}")
    return root


def _parse_src(src: str, opts: dict) -> tuple[str, str | None, str]:
    """Resolve (method, host, root) from the package src URL, with per-field option overrides.

    Args:
        src(str): Package source URL (e.g. "http://deb.debian.org/debian").
        opts(dict): Package sync options; "method"/"host"/"root" override the
            corresponding value parsed from src.

    Return:
        parsed(tuple[str, str | None, str]): Validated (method, host, root).

    Raises:
        ValueError: A part cannot be resolved from src or options, or fails validation.
    """
    parsed = urllib.parse.urlparse(src) if src else None

    method = opts.get("method") or (parsed.scheme if parsed else None)
    if not method:
        raise ValueError("debmirror method: could not be resolved from src or options.method")
    method = _validate_token(method, "method", _METHOD_TOKEN_RE)
    _validate_enum(method, _METHODS, "method")

    if method == "file":
        if parsed is None or parsed.scheme != "file":
            raise ValueError("debmirror src: file method requires a file: URL")
        if "host" in opts:
            raise ValueError("debmirror host: options.host is not supported for file method")
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raise ValueError("debmirror host: file URL authority must be empty or localhost")
        if parsed.query or parsed.fragment:
            raise ValueError("debmirror src: file URL must not contain a query or fragment")
        if _MALFORMED_PERCENT_RE.search(parsed.path):
            raise ValueError("debmirror src: file URL contains malformed percent escape")

        root_opt = opts.get("root")
        root = root_opt if root_opt is not None else urllib.parse.unquote(parsed.path)
        return method, None, _validate_file_root(root)

    host = opts.get("host") or (parsed.netloc if parsed else None)
    if not host:
        raise ValueError("debmirror host: could not be resolved from src or options.host")
    host = _validate_token(host, "host", _HOST_RE)

    root = opts.get("root")
    if root is None and parsed is not None:
        root = parsed.path.lstrip("/")
    if not root:
        raise ValueError("debmirror root: could not be resolved from src or options.root")
    root = _validate_token(root, "root", _ROOT_RE)

    return method, host, root


def build_command(package: mirror.structure.Package) -> tuple[list, dict]:
    """Build the debmirror argv list and environment dictionary for a package.

    Pure builder: does not touch the network, filesystem, or worker. Omitted
    selection flags must be resolved by the discovery helper before execution.

    Args:
        package(mirror.structure.Package): Package to sync.

    Return:
        result(tuple[list[str], dict[str, str]]): Command argument list (mirrordir last)
            and environment dict.

    Raises:
        ValueError: Any option fails validation or a required option is missing.
    """
    opts = package.settings.options
    method, host, root = _parse_src(package.settings.src, opts)

    argv = [
        "debmirror", "--verbose", "--method", method,
    ]
    if host is not None:
        argv += ["--host", host]
    argv += ["--root", root]

    for key, pattern in (("dist", _DIST_RE), ("section", _SECTION_RE), ("arch", _DIST_RE)):
        if key in opts:
            argv += [f"--{key}", _join_multi(opts[key], key, pattern)]

    argv.append("--source" if opts.get("source") else "--nosource")

    check_gpg = opts.get("check_gpg", True)
    if check_gpg:
        argv.append("--check-gpg")
        keyring_opt = opts.get("keyring")
        if keyring_opt:
            keyrings = keyring_opt if isinstance(keyring_opt, list) else [keyring_opt]
            for keyring in keyrings:
                argv += ["--keyring", _validate_keyring(keyring)]
        if opts.get("ignore_release_gpg"):
            argv.append("--ignore-release-gpg")
    else:
        argv.append("--no-check-gpg")

    if opts.get("ignore_missing_release"):
        argv.append("--ignore-missing-release")

    cleanup = _validate_enum(opts.get("cleanup", "postcleanup"), _CLEANUP_MODES, "cleanup")
    argv.append(f"--{cleanup}")

    diff_opt = opts.get("diff")
    if diff_opt:
        argv += ["--diff", _validate_enum(diff_opt, _DIFF_MODES, "diff")]

    rsync_extra_opt = opts.get("rsync_extra")
    if rsync_extra_opt:
        rsync_extra = _join_multi(rsync_extra_opt, "rsync_extra", _RSYNC_EXTRA_TOKEN_RE)
        for item in rsync_extra.split(","):
            _validate_enum(item, _RSYNC_EXTRA_CHOICES, "rsync_extra")
        argv += ["--rsync-extra", rsync_extra]

    if opts.get("i18n"):
        argv.append("--i18n")
    if opts.get("getcontents"):
        argv.append("--getcontents")

    di_dist_opt = opts.get("di_dist")
    if di_dist_opt:
        argv += ["--di-dist", _join_multi(di_dist_opt, "di_dist", _DIST_RE)]
    di_arch_opt = opts.get("di_arch")
    if di_arch_opt:
        argv += ["--di-arch", _join_multi(di_arch_opt, "di_arch", _DIST_RE)]

    proxy_opt = opts.get("proxy")
    if proxy_opt:
        proxy = _no_ctrl(str(proxy_opt), "proxy")
        if any(ch.isspace() for ch in proxy):
            raise ValueError("debmirror proxy: must not contain whitespace")
        argv += ["--proxy", proxy]
    if opts.get("passive"):
        argv.append("--passive")

    env = dict(mirror.sync.get_extra_args(package.pkgid))

    user = opts.get("user")
    password = opts.get("password")
    if user:
        user = _validate_token(str(user), "user", _USER_RE)
        if method == "ftp":
            argv += ["--user", user]
            if password:
                argv += ["--passwd", _no_ctrl(str(password), "passwd")]
        elif method == "rsync":
            env["RSYNC_PASSWORD"] = _no_ctrl(str(password), "password") if password else ""
            argv[argv.index("--host") + 1] = f"{user}@{host}"
        # http/https/file have no inline basic auth: the user is validated above
        # but not emitted here; execute() logs the ignored-credentials warning.

    for key, flag in (
        ("exclude", "--exclude"),
        ("include", "--include"),
        ("exclude_deb_section", "--exclude-deb-section"),
        ("limit_priority", "--limit-priority"),
    ):
        _extend_filter_args(argv, opts, key, flag)

    rsync_options_opt = opts.get("rsync_options")
    if rsync_options_opt:
        argv += ["--rsync-options", _no_ctrl(str(rsync_options_opt), "rsync_options")]

    timeout_opt = opts.get("timeout")
    if timeout_opt is not None:
        if isinstance(timeout_opt, bool) or not isinstance(timeout_opt, int) or timeout_opt <= 0:
            raise ValueError(f"debmirror timeout: must be a positive integer, got {timeout_opt!r}")
        argv += ["--timeout", str(timeout_opt)]

    if opts.get("allow_dist_rename"):
        argv.append("--allow-dist-rename")
    if opts.get("omit_suite_symlinks"):
        argv.append("--omit-suite-symlinks")

    argv.append(_validate_dst(package.settings.dst))

    return argv, env


def _redact_command(command: list) -> str:
    """Return a log-safe, space-joined copy of command with the --passwd value masked."""
    redacted = list(command)
    for i, arg in enumerate(redacted):
        if arg == "--passwd" and i + 1 < len(redacted):
            redacted[i + 1] = "***"
    return " ".join(redacted)


def execute(package: mirror.structure.Package, logger: logging.Logger, trigger: str = "auto") -> None:
    """Run debmirror sync for the given package.

    Args:
        package(mirror.structure.Package): Package to sync.
        logger(logging.Logger): Logger for this sync session.
        trigger(str): Source of the sync trigger. Accepted for interface parity
            with other sync modules; debmirror has no trigger concept and does
            not use this value.
    """
    logger.info(f"Starting sync.debmirror for {package.name}")

    if not mirror.toolbox.command_exists("debmirror"):
        logger.error("debmirror binary not found on PATH")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)
        return

    try:
        argv, env = build_command(package)

        # Warnings live here (not in the pure builder). Ignored HTTP/HTTPS/file
        # credentials: debmirror has no inline basic auth for these methods.
        method = argv[argv.index("--method") + 1]
        if package.settings.options.get("user") and method not in ("ftp", "rsync"):
            logger.warning(
                f"debmirror package {package.pkgid}: method {method!r} has no inline basic auth; "
                "user/password ignored"
            )
        # GPG: warn when verification is on but no keyring is configured, and when
        # a configured keyring file is missing on disk.
        if "--check-gpg" in argv and "--keyring" not in argv:
            logger.warning(
                f"debmirror package {package.pkgid}: check_gpg is enabled but no keyring is configured"
            )
        for idx, arg in enumerate(argv):
            if arg == "--keyring" and idx + 1 < len(argv) and not Path(argv[idx + 1]).is_file():
                logger.warning(f"debmirror: configured keyring not found: {argv[idx + 1]}")

        log_path = None
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        logger.info(f"Running debmirror: {_redact_command(argv)}")
        argv = [
            sys.executable, "-c", "from mirror.sync.debmirror import main; main()", "--", *argv,
        ]

        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="debmirror",
            commandline=argv,
            env=env,
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=log_path,
        )
    except Exception as e:
        logger.error(f"Sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def plugin():
    """Entry-point factory for the debmirror plug-in.

    Return:
        record(mirror.plugin.PluginRecord): Sync plug-in record exposing execute.
    """
    from mirror.plugin import sync_plugin
    return sync_plugin(name="debmirror", execute=execute)


_LOGGER = logging.getLogger(__name__)
_MAX_METADATA_SIZE = 16 * 1024 * 1024
_MAX_DISTRIBUTIONS = 1024
_MAX_LISTING_LINKS = 4096
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]*$")
_FIELD_RE = re.compile(rb"^([A-Za-z0-9-]+):[ \t]*(.*)$")
_VALUE_OPTIONS = {
    "--arch", "--config-file", "--di-arch", "--di-dist", "--diff",
    "--dist", "--exclude", "--exclude-deb-section", "--host", "--include",
    "--keyring", "--limit-priority", "--method", "--passwd", "--proxy",
    "--root", "--rsync-extra", "--rsync-options", "--section", "--timeout",
    "--user",
}
_ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None


def _limit_child_file_size() -> None:
    """Limit files written by metadata subprocesses."""
    current_soft, current_hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    limit = _MAX_METADATA_SIZE
    if current_soft != resource.RLIM_INFINITY:
        limit = min(limit, current_soft)
    if current_hard != resource.RLIM_INFINITY:
        limit = min(limit, current_hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, current_hard))


class DiscoveryError(RuntimeError):
    """Raised when repository metadata cannot be discovered safely."""


class ResourceMissing(DiscoveryError):
    """Raised when a requested remote resource does not exist."""


@dataclass(frozen=True)
class NativeOptions:
    """Parsed debmirror options needed by discovery."""

    method: str
    host: str | None
    root: str
    dist: str | None
    section: str | None
    arch: str | None
    source: bool
    check_gpg: bool
    ignore_release_gpg: bool
    keyrings: tuple[str, ...]
    user: str | None
    password: str | None
    proxy: str | None
    passive: bool
    rsync_options: tuple[str, ...]
    timeout: int
    deadline: float


@dataclass(frozen=True)
class ReleaseMetadata:
    """Validated discovery fields from one Release file."""

    path: str
    codename: str
    native_name: str
    components: tuple[str, ...]
    architectures: tuple[str, ...]
    digest: str


class _LinkParser(html.parser.HTMLParser):
    """Collect href attributes from an HTTP directory listing."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value is not None:
                if len(self.hrefs) >= _MAX_LISTING_LINKS:
                    raise DiscoveryError(
                        "repository dists listing has too many entries; set options.dist explicitly"
                    )
                self.hrefs.append(value)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirect chains that downgrade TLS or forward URL credentials."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request | None:
        old = urllib.parse.urlsplit(request.full_url)
        new = urllib.parse.urlsplit(new_url)
        if old.scheme == "https" and new.scheme != "https":
            raise DiscoveryError("repository redirect would downgrade HTTPS")
        if (old.username or old.password) and old.hostname != new.hostname:
            raise DiscoveryError("repository redirect would forward credentials to another host")
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def _scan_options(argv: list[str]) -> list[tuple[str, str | None]]:
    """Parse known native flags while respecting each option's arity."""
    parsed = []
    index = 1
    while index < len(argv) - 1:
        item = argv[index]
        name, separator, inline_value = item.partition("=")
        if name in _VALUE_OPTIONS:
            if separator:
                parsed.append((name, inline_value))
                index += 1
                continue
            if index + 1 >= len(argv) - 1:
                raise DiscoveryError(f"native option {name} has no value")
            parsed.append((name, argv[index + 1]))
            index += 2
            continue
        parsed.append((item, None))
        index += 1
    return parsed


def _read_flag(parsed: list[tuple[str, str | None]], name: str) -> str | None:
    """Return the final value of a parsed native long option."""
    values = [value for option, value in parsed if option == name]
    return values[-1] if values else None


def _has_flag(parsed: list[tuple[str, str | None]], name: str) -> bool:
    """Return whether a native long option is present."""
    return any(option == name for option, _ in parsed)


def _parse_timeout(value: str | None) -> int:
    """Parse the native timeout used by discovery transports."""
    if value is None:
        return 300
    try:
        timeout = int(value)
    except ValueError as error:
        raise DiscoveryError("native --timeout must be a positive integer") from error
    if timeout <= 0:
        raise DiscoveryError("native --timeout must be a positive integer")
    return timeout


def _parse_native_options(argv: list[str]) -> NativeOptions:
    """Parse the subset of the debmirror command line used for discovery."""
    if len(argv) < 2 or Path(argv[0]).name != "debmirror":
        raise DiscoveryError("discovery helper requires a debmirror command")
    parsed = _scan_options(argv)
    method = _read_flag(parsed, "--method")
    root = _read_flag(parsed, "--root")
    if method not in {"http", "https", "ftp", "file", "rsync"}:
        raise DiscoveryError("native --method is missing or unsupported")
    if not root:
        raise DiscoveryError("native --root is missing")
    host = _read_flag(parsed, "--host")
    if method != "file" and not host:
        raise DiscoveryError("native --host is missing")
    timeout = _parse_timeout(_read_flag(parsed, "--timeout"))
    return NativeOptions(
        method=method,
        host=host,
        root=root,
        dist=_read_flag(parsed, "--dist"),
        section=_read_flag(parsed, "--section"),
        arch=_read_flag(parsed, "--arch"),
        source=_has_flag(parsed, "--source") and not _has_flag(parsed, "--nosource"),
        check_gpg=not _has_flag(parsed, "--no-check-gpg"),
        ignore_release_gpg=_has_flag(parsed, "--ignore-release-gpg"),
        keyrings=tuple(value for name, value in parsed if name == "--keyring" and value is not None),
        user=_read_flag(parsed, "--user"),
        password=_read_flag(parsed, "--passwd"),
        proxy=_read_flag(parsed, "--proxy"),
        passive=_has_flag(parsed, "--passive"),
        rsync_options=tuple(shlex.split(_read_flag(parsed, "--rsync-options") or "")),
        timeout=timeout,
        deadline=time.monotonic() + max(300, timeout),
    )


def _remaining_timeout(options: NativeOptions) -> float:
    """Limit one operation by both its timeout and the discovery deadline."""
    remaining = options.deadline - time.monotonic()
    if remaining <= 0:
        raise DiscoveryError("repository discovery timed out; set options.dist explicitly")
    return min(options.timeout, remaining)


def _validate_name(value: str, label: str, pattern: re.Pattern[str] = _TOKEN_RE) -> str:
    """Validate a repository path token."""
    segments = value.split("/")
    if (
        not pattern.fullmatch(value)
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise DiscoveryError(f"repository metadata contains an invalid {label}")
    return value


def _split_values(value: str, label: str, pattern: re.Pattern[str] = _TOKEN_RE) -> tuple[str, ...]:
    """Split and validate a comma-separated native option."""
    values = tuple(value.split(","))
    if not values:
        raise DiscoveryError(f"native {label} is empty")
    return tuple(_validate_name(item, label, pattern) for item in values)


def _bounded(data: bytes, label: str) -> bytes:
    """Reject oversized metadata responses."""
    if len(data) > _MAX_METADATA_SIZE:
        raise DiscoveryError(f"repository {label} exceeds the 16 MiB limit")
    return data


def _url(options: NativeOptions, relative: str) -> str:
    """Build a safely quoted repository URL."""
    root = options.root.strip("/")
    path = posixpath.join(root, relative)
    quoted = urllib.parse.quote(path, safe="/@:+~")
    authority = options.host
    if options.method == "ftp" and options.user:
        user = urllib.parse.quote(options.user, safe="")
        password = urllib.parse.quote(options.password or "", safe="")
        authority = f"{user}:{password}@{authority}"
    return f"{options.method}://{authority}/{quoted}"


def _http_open(options: NativeOptions, relative: str) -> bytes:
    """Fetch one HTTP(S) repository resource."""
    handlers: list[urllib.request.BaseHandler] = [_SafeRedirectHandler()]
    if options.proxy:
        handlers.append(urllib.request.ProxyHandler({options.method: options.proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(_url(options, relative), timeout=_remaining_timeout(options)) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > _MAX_METADATA_SIZE:
                raise DiscoveryError("repository metadata exceeds the 16 MiB limit")
            return _bounded(response.read(_MAX_METADATA_SIZE + 1), "metadata")
    except urllib.error.HTTPError as error:
        if error.code in {404, 410}:
            raise ResourceMissing("repository resource is missing") from error
        raise DiscoveryError(f"repository HTTP request failed with status {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise DiscoveryError("repository URL request failed") from error


def _ftp_connect(options: NativeOptions) -> ftplib.FTP:
    """Open and authenticate an FTP connection."""
    assert options.host is not None
    host, separator, port_text = options.host.rpartition(":")
    if separator and port_text.isdigit():
        hostname, port = host, int(port_text)
    else:
        hostname, port = options.host, 21
    try:
        ftp = ftplib.FTP()
        ftp.connect(hostname, port, timeout=_remaining_timeout(options))
        ftp.login(options.user or "anonymous", options.password or "anonymous@")
        ftp.set_pasv(options.passive)
        return ftp
    except (OSError, ftplib.Error) as error:
        raise DiscoveryError("repository FTP connection failed") from error


def _ftp_path(options: NativeOptions, relative: str) -> str:
    """Build a contained absolute FTP path."""
    return "/" + posixpath.join(options.root.strip("/"), relative)


def _ftp_fetch(options: NativeOptions, relative: str) -> bytes:
    """Fetch one FTP repository resource."""
    data = bytearray()
    ftp = _ftp_connect(options)

    def append_chunk(chunk: bytes) -> None:
        data.extend(chunk)
        if len(data) > _MAX_METADATA_SIZE:
            raise DiscoveryError("repository metadata exceeds the 16 MiB limit")

    try:
        ftp.retrbinary(f"RETR {_ftp_path(options, relative)}", append_chunk)
        return bytes(data)
    except ftplib.error_perm as error:
        message = str(error).lower()
        if str(error).startswith("550") and any(
            marker in message for marker in ("not found", "no such file", "does not exist")
        ):
            raise ResourceMissing("repository resource is missing") from error
        raise DiscoveryError("repository FTP request failed") from error
    except (OSError, ftplib.Error) as error:
        raise DiscoveryError("repository FTP request failed") from error
    finally:
        try:
            ftp.quit()
        except (OSError, ftplib.Error):
            ftp.close()


def _file_path(options: NativeOptions, relative: str) -> Path:
    """Resolve a local repository path while containing traversal and symlinks."""
    root = Path(options.root).resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise DiscoveryError("repository path escapes the configured root")
    return target


def _file_fetch(options: NativeOptions, relative: str) -> bytes:
    """Fetch one local repository resource."""
    path = _file_path(options, relative)
    try:
        with path.open("rb") as resource:
            return _bounded(resource.read(_MAX_METADATA_SIZE + 1), "metadata")
    except FileNotFoundError as error:
        raise ResourceMissing("repository resource is missing") from error
    except OSError as error:
        raise DiscoveryError("local repository read failed") from error


def _rsync_source(options: NativeOptions, relative: str) -> str:
    """Build a contained rsync source string."""
    root = options.root.strip("/")
    return f"rsync://{options.host}/{posixpath.join(root, relative)}"


def _run_process(
    command: list[str], timeout: float, stdout: int | None = subprocess.DEVNULL
) -> subprocess.CompletedProcess[bytes]:
    """Run a child process while exposing it to the helper's signal handler."""
    global _ACTIVE_PROCESS

    try:
        _ACTIVE_PROCESS = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            preexec_fn=_limit_child_file_size,
        )
        output, _ = _ACTIVE_PROCESS.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, _ACTIVE_PROCESS.returncode, output, b"")
    except subprocess.TimeoutExpired as error:
        assert _ACTIVE_PROCESS is not None
        _ACTIVE_PROCESS.kill()
        _ACTIVE_PROCESS.communicate()
        raise DiscoveryError("repository command timed out") from error
    except OSError as error:
        raise DiscoveryError("repository command could not run") from error
    finally:
        _ACTIVE_PROCESS = None


def _terminate_child(signum: int, frame: object) -> None:
    """Terminate a metadata child before stopping the wrapper process."""
    del frame
    if _ACTIVE_PROCESS is not None:
        _ACTIVE_PROCESS.terminate()
        try:
            _ACTIVE_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _ACTIVE_PROCESS.kill()
            _ACTIVE_PROCESS.wait()
    raise SystemExit(128 + signum)


def _run_rsync(options: NativeOptions, source: str, destination: str | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run rsync with bounded time and inherited credentials."""
    command = ["rsync", "--timeout", str(options.timeout), *options.rsync_options]
    if destination is None:
        command += ["--list-only", source]
    else:
        command += [f"--max-size={_MAX_METADATA_SIZE}", source, destination]
    try:
        return _run_process(command, _remaining_timeout(options))
    except DiscoveryError as error:
        raise DiscoveryError("repository rsync request failed") from error


def _rsync_fetch(options: NativeOptions, relative: str) -> bytes:
    """Fetch one rsync repository resource."""
    parent, _, name = relative.rpartition("/")
    listing = _rsync_listing(options, f"{parent}/")
    listed_names = {
        parts[-1]
        for line in listing.splitlines()
        if len(parts := line.decode("utf-8", "replace").split(maxsplit=4)) == 5
    }
    if name not in listed_names:
        raise ResourceMissing("repository resource is missing")
    with tempfile.TemporaryDirectory(prefix="mirror-debmirror-discovery-") as directory:
        destination = Path(directory) / "resource"
        result = _run_rsync(options, _rsync_source(options, relative), str(destination))
        if result.returncode != 0:
            raise DiscoveryError("repository rsync request failed")
        try:
            if destination.stat().st_size > _MAX_METADATA_SIZE:
                raise DiscoveryError("repository metadata exceeds the 16 MiB limit")
            return destination.read_bytes()
        except FileNotFoundError as error:
            raise DiscoveryError("repository metadata was not downloaded") from error


def _fetch(options: NativeOptions, relative: str) -> bytes:
    """Fetch one resource using the configured debmirror transport."""
    if options.method in {"http", "https"} or (
        options.method == "ftp" and (options.proxy or os.environ.get("ftp_proxy"))
    ):
        return _http_open(options, relative)
    if options.method == "ftp":
        return _ftp_fetch(options, relative)
    if options.method == "file":
        return _file_fetch(options, relative)
    return _rsync_fetch(options, relative)


def _http_list(options: NativeOptions) -> tuple[str, ...]:
    """Parse direct child directories from an HTTP directory listing."""
    relative = "dists/"
    data = _http_open(options, relative)
    parser = _LinkParser()
    try:
        parser.feed(data.decode("utf-8", "strict"))
    except UnicodeDecodeError as error:
        raise DiscoveryError("repository dists listing is not valid HTML") from error
    base = urllib.parse.urlsplit(_url(options, relative))
    base_path = urllib.parse.unquote(base.path).rstrip("/") + "/"
    names: set[str] = set()
    for href in parser.hrefs:
        target = urllib.parse.urlsplit(urllib.parse.urljoin(_url(options, relative), href))
        if (
            target.scheme != base.scheme
            or target.netloc != base.netloc
            or target.query
            or target.fragment
        ):
            continue
        target_path = urllib.parse.unquote(target.path)
        if not target_path.endswith("/") or not target_path.startswith(base_path):
            continue
        remainder = target_path[len(base_path):].strip("/")
        if "/" not in remainder and _TOKEN_RE.fullmatch(remainder):
            names.add(remainder)
    return tuple(sorted(names))


def _ftp_list(options: NativeOptions) -> tuple[str, ...]:
    """List direct distribution directories over FTP."""
    ftp = _ftp_connect(options)
    path = _ftp_path(options, "dists")
    entries: list[str] = []

    def add_entry(name: str) -> None:
        candidate = posixpath.basename(name.rstrip("/"))
        if not _TOKEN_RE.fullmatch(candidate):
            return
        entries.append(candidate)
        if len(entries) > _MAX_DISTRIBUTIONS:
            raise DiscoveryError(
                "repository has too many distributions; set options.dist explicitly"
            )

    try:
        try:
            for name, facts in ftp.mlsd(path):
                if facts.get("type") in {"dir", "cdir"}:
                    add_entry(name)
        except AttributeError:
            ftp.retrlines(f"NLST {path}", add_entry)
        except ftplib.error_perm as error:
            if not str(error).startswith(("500", "501", "502", "504")):
                raise
            ftp.retrlines(f"NLST {path}", add_entry)
        return tuple(sorted(set(entries)))
    except (OSError, ftplib.Error) as error:
        raise DiscoveryError("repository dists listing failed; set options.dist explicitly") from error
    finally:
        try:
            ftp.quit()
        except (OSError, ftplib.Error):
            ftp.close()


def _file_list(options: NativeOptions) -> tuple[str, ...]:
    """List direct distribution directories from a local repository."""
    dists = _file_path(options, "dists")
    try:
        names = []
        for entry in dists.iterdir():
            if not _TOKEN_RE.fullmatch(entry.name) or not entry.is_dir():
                continue
            resolved = entry.resolve()
            if resolved.parent == dists.resolve():
                names.append(entry.name)
                if len(names) > _MAX_DISTRIBUTIONS:
                    raise DiscoveryError(
                        "repository has too many distributions; set options.dist explicitly"
                    )
        return tuple(sorted(names))
    except OSError as error:
        raise DiscoveryError("repository dists listing failed; set options.dist explicitly") from error


def _rsync_listing(options: NativeOptions, relative: str) -> bytes:
    """Return a bounded rsync directory listing."""
    with tempfile.TemporaryFile() as output:
        global _ACTIVE_PROCESS
        command = [
            "rsync", "--timeout", str(options.timeout), *options.rsync_options,
            "--list-only", _rsync_source(options, relative),
        ]
        try:
            _ACTIVE_PROCESS = subprocess.Popen(
                command,
                stdout=output,
                stderr=subprocess.DEVNULL,
                preexec_fn=_limit_child_file_size,
            )
            _ACTIVE_PROCESS.communicate(timeout=_remaining_timeout(options))
            returncode = _ACTIVE_PROCESS.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            if _ACTIVE_PROCESS is not None:
                _ACTIVE_PROCESS.kill()
                _ACTIVE_PROCESS.communicate()
            raise DiscoveryError("repository rsync listing failed") from error
        finally:
            _ACTIVE_PROCESS = None
        output.seek(0)
        listing = _bounded(output.read(_MAX_METADATA_SIZE + 1), "dists listing")
    if returncode != 0:
        raise DiscoveryError("repository rsync listing failed")
    return listing


def _rsync_list(options: NativeOptions) -> tuple[str, ...]:
    """List direct distribution directories over rsync."""
    try:
        listing = _rsync_listing(options, "dists/")
    except DiscoveryError as error:
        raise DiscoveryError("repository dists listing failed; set options.dist explicitly") from error
    names = set()
    for raw_line in listing.splitlines():
        parts = raw_line.decode("utf-8", "replace").split(maxsplit=4)
        if len(parts) == 5 and parts[0].startswith("d"):
            name = parts[-1].rstrip("/")
            if _TOKEN_RE.fullmatch(name):
                names.add(name)
    return tuple(sorted(names))


def _list_distributions(options: NativeOptions) -> tuple[str, ...]:
    """List safe direct children of the repository dists directory."""
    if options.method in {"http", "https"} or (
        options.method == "ftp" and (options.proxy or os.environ.get("ftp_proxy"))
    ):
        values = _http_list(options)
    elif options.method == "ftp":
        values = _ftp_list(options)
    elif options.method == "file":
        values = _file_list(options)
    else:
        values = _rsync_list(options)
    if not values:
        raise DiscoveryError("no distributions found; set options.dist explicitly")
    if len(values) > _MAX_DISTRIBUTIONS:
        raise DiscoveryError("too many distributions found; set options.dist explicitly")
    return values


def _extract_inrelease(data: bytes) -> bytes:
    """Extract cleartext from a syntactically valid clearsigned InRelease."""
    lines = data.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"-----BEGIN PGP SIGNED MESSAGE-----":
        raise DiscoveryError("repository InRelease is not a clearsigned message")
    index = 1
    while index < len(lines) and lines[index].rstrip(b"\r\n"):
        if b":" not in lines[index]:
            raise DiscoveryError("repository InRelease has malformed armor headers")
        index += 1
    if index >= len(lines):
        raise DiscoveryError("repository InRelease has no signed content")
    index += 1
    content = []
    while index < len(lines):
        line = lines[index]
        if line.rstrip(b"\r\n") == b"-----BEGIN PGP SIGNATURE-----":
            if not content:
                raise DiscoveryError("repository InRelease has empty signed content")
            return b"".join(item[2:] if item.startswith(b"- ") else item for item in content)
        content.append(line)
        index += 1
    raise DiscoveryError("repository InRelease has no signature block")


def _verify_gpg(options: NativeOptions, signed: bytes, signature: bytes | None = None) -> bytes:
    """Verify Release metadata with gpgv and return its cleartext content."""
    with tempfile.TemporaryDirectory(prefix="mirror-debmirror-gpg-") as directory:
        signed_path = Path(directory) / "signed"
        signed_path.write_bytes(signed)
        command = ["gpgv"]
        for keyring in options.keyrings:
            command += ["--keyring", keyring]
        if signature is None:
            output_path = Path(directory) / "release"
            command += ["--output", str(output_path), str(signed_path)]
        else:
            signature_path = Path(directory) / "signature"
            signature_path.write_bytes(signature)
            output_path = None
            command += [str(signature_path), str(signed_path)]
        try:
            result = _run_process(command, _remaining_timeout(options))
        except DiscoveryError as error:
            raise DiscoveryError("Release signature verification could not run") from error
        if result.returncode != 0:
            raise DiscoveryError("Release signature verification failed")
        return output_path.read_bytes() if output_path is not None else signed


def _read_release(options: NativeOptions, dist: str) -> bytes:
    """Fetch and optionally verify one distribution's Release metadata."""
    prefix = f"dists/{dist}"
    try:
        inrelease = _fetch(options, f"{prefix}/InRelease")
    except ResourceMissing:
        release = _fetch(options, f"{prefix}/Release")
        if not options.check_gpg:
            return release
        try:
            signature = _fetch(options, f"{prefix}/Release.gpg")
            return _verify_gpg(options, release, signature)
        except ResourceMissing as error:
            if options.ignore_release_gpg:
                return release
            raise DiscoveryError(f"distribution {dist} has no Release.gpg signature") from error
        except DiscoveryError:
            if options.ignore_release_gpg:
                return release
            raise
    if not options.check_gpg:
        return _extract_inrelease(inrelease)
    try:
        return _verify_gpg(options, inrelease)
    except DiscoveryError:
        if options.ignore_release_gpg:
            return _extract_inrelease(inrelease)
        raise


def _parse_fields(data: bytes) -> dict[str, str]:
    """Parse the top-level fields in Debian Release metadata."""
    wanted = {"Origin", "Suite", "Codename", "Components", "Architectures"}
    fields: dict[str, list[str]] = {}
    current = None
    for line in data.splitlines():
        if line.startswith((b" ", b"\t")):
            if current in wanted:
                try:
                    fields[current].append(line.strip().decode("utf-8", "strict"))
                except UnicodeDecodeError as error:
                    raise DiscoveryError("repository Release metadata is not valid UTF-8") from error
            continue
        match = _FIELD_RE.match(line)
        if match is None:
            continue
        current = match.group(1).decode("ascii")
        if current not in wanted:
            continue
        try:
            value = match.group(2).decode("utf-8", "strict").strip()
        except UnicodeDecodeError as error:
            raise DiscoveryError("repository Release metadata is not valid UTF-8") from error
        if current in fields:
            raise DiscoveryError(f"repository Release contains duplicate {current} field")
        fields[current] = [value]
    return {name: " ".join(parts) for name, parts in fields.items()}


def _metadata(dist: str, data: bytes, need_components: bool, need_architectures: bool) -> ReleaseMetadata:
    """Validate fields used to select automatic debmirror options."""
    fields = _parse_fields(data)
    codename_value = fields.get("Codename")
    if not codename_value:
        raise DiscoveryError(f"distribution {dist} Release has no Codename field")
    codename = _validate_name(codename_value, "Codename")
    origin = fields.get("Origin", "")
    suite = fields.get("Suite")
    if origin in {"Ubuntu", "Canonical", "UbuntuESM", "UbuntuESMApps"}:
        if not suite:
            raise DiscoveryError(f"distribution {dist} Ubuntu Release has no Suite field")
        native_name = _validate_name(suite, "Suite")
    else:
        native_name = codename
    component_value = fields.get("Components")
    if need_components and not component_value:
        raise DiscoveryError(f"distribution {dist} Release has no Components field")
    architecture_value = fields.get("Architectures")
    if need_architectures and not architecture_value:
        raise DiscoveryError(f"distribution {dist} Release has no Architectures field")
    components = tuple(
        _validate_name(item, "component", _COMPONENT_RE)
        for item in component_value.split()
    ) if component_value else ()
    architectures = tuple(
        _validate_name(item, "architecture")
        for item in architecture_value.split()
    ) if architecture_value else ()
    if need_components and not components:
        raise DiscoveryError(f"distribution {dist} Release has no components")
    if need_architectures and not architectures:
        raise DiscoveryError(f"distribution {dist} Release has no architectures")
    return ReleaseMetadata(
        path=dist,
        codename=codename,
        native_name=native_name,
        components=components,
        architectures=architectures,
        digest=hashlib.sha256(data).hexdigest(),
    )


def _select_auto_distributions(metadata: list[ReleaseMetadata]) -> list[ReleaseMetadata]:
    """Deduplicate aliases and reject ambiguous native codenames."""
    by_codename: dict[str, set[str]] = {}
    for item in metadata:
        by_codename.setdefault(item.native_name, set()).add(item.digest)
    collisions = sorted(name for name, digests in by_codename.items() if len(digests) > 1)
    if collisions:
        raise DiscoveryError(f"automatic distribution discovery found conflicting Codename {collisions[0]}")
    by_digest: dict[str, list[ReleaseMetadata]] = {}
    for item in metadata:
        by_digest.setdefault(item.digest, []).append(item)
    selected = []
    for group in by_digest.values():
        exact = [item for item in group if item.path == item.native_name]
        selected.append(min(exact or group, key=lambda item: item.path))
    return sorted(selected, key=lambda item: item.path)


def _discover_metadata(options: NativeOptions) -> list[ReleaseMetadata]:
    """Fetch metadata for explicit or automatically listed distributions."""
    automatic = options.dist is None
    dists = _list_distributions(options) if automatic else _split_values(options.dist, "--dist")
    values = []
    for dist in dists:
        _remaining_timeout(options)
        try:
            data = _read_release(options, dist)
        except ResourceMissing:
            if automatic:
                continue
            raise DiscoveryError(f"distribution {dist} has no InRelease or Release metadata")
        values.append(_metadata(dist, data, options.section is None, options.arch is None))
    if not values:
        raise DiscoveryError("no distributions with usable Release metadata found; set options.dist explicitly")
    return _select_auto_distributions(values) if automatic else values


def _binary_architectures(metadata: list[ReleaseMetadata], source: bool) -> tuple[str, ...]:
    """Select binary architectures, handling source-only and all-only archives."""
    raw = {item for release in metadata for item in release.architectures}
    if "none" in raw:
        raise DiscoveryError("repository Release contains reserved architecture none")
    binary = tuple(sorted(raw - {"all", "source"}))
    if binary:
        return binary
    if "all" in raw or ("source" in raw and source):
        return ("none",)
    if "source" in raw:
        raise DiscoveryError("repository contains only source packages; set options.source to true")
    raise DiscoveryError("repository has no usable binary architectures")


def _insert_options(argv: list[str], additions: list[str]) -> list[str]:
    """Insert discovered options immediately before debmirror's destination."""
    if not additions:
        return list(argv)
    return [*argv[:-1], *additions, argv[-1]]


def _guard_existing_distributions(destination: str, metadata: list[ReleaseMetadata]) -> None:
    """Fail if partial automatic discovery would remove an existing distribution."""
    dists = Path(destination) / "dists"
    if not dists.exists():
        return
    try:
        existing = {
            entry.name
            for entry in dists.iterdir()
            if entry.is_dir() and not entry.is_symlink() and _TOKEN_RE.fullmatch(entry.name)
        }
    except OSError as error:
        raise DiscoveryError("existing destination distributions could not be inspected") from error
    selected = {item.native_name for item in metadata}
    if existing - selected:
        raise DiscoveryError(
            "existing distributions are missing from automatic discovery; "
            "set options.dist explicitly to change selection"
        )


def resolve_command(argv: list[str]) -> list[str]:
    """Resolve omitted debmirror dimensions and return a native command.

    Args:
        argv: Native debmirror argument vector with its destination last.

    Returns:
        Native argument vector containing every required selection option.
    """
    options = _parse_native_options(argv)
    if options.dist is not None and options.section is not None and options.arch is not None:
        return list(argv)
    metadata = _discover_metadata(options)
    if options.dist is None:
        _guard_existing_distributions(argv[-1], metadata)
    additions = []
    if options.dist is None:
        additions += ["--dist", ",".join(item.path for item in metadata)]
    if options.section is None:
        sections = sorted({item for release in metadata for item in release.components})
        additions += ["--section", ",".join(sections)]
    if options.arch is None:
        additions += ["--arch", ",".join(_binary_architectures(metadata, options.source))]
    resolved = _insert_options(argv, additions)
    parsed = _scan_options(resolved)
    _LOGGER.info(
        "debmirror discovery: dist=%s section=%s arch=%s",
        _read_flag(parsed, "--dist"),
        _read_flag(parsed, "--section"),
        _read_flag(parsed, "--arch"),
    )
    return resolved


def _run_native(argv: list[str]) -> int:
    """Run debmirror with an isolated config and clean up after completion."""
    global _ACTIVE_PROCESS

    with tempfile.TemporaryDirectory(prefix="mirror-debmirror-run-") as directory:
        config = Path(directory) / "debmirror.conf"
        config.write_text("1;\n", encoding="utf-8")
        command = [*argv[:-1], "--config-file", str(config), argv[-1]]
        try:
            # Native downloads must not inherit the metadata subprocess size cap.
            _ACTIVE_PROCESS = subprocess.Popen(command)
            return _ACTIVE_PROCESS.wait()
        except OSError as error:
            raise DiscoveryError("debmirror could not be started") from error
        finally:
            _ACTIVE_PROCESS = None


def main() -> None:
    """Discover selections and run debmirror in the worker's environment."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    signal.signal(signal.SIGTERM, _terminate_child)
    native = sys.argv[1:]
    if native[:1] == ["--"]:
        native = native[1:]
    try:
        resolved = resolve_command(native)
        returncode = _run_native(resolved)
    except DiscoveryError as error:
        _LOGGER.error("debmirror discovery failed: %s", error)
        raise SystemExit(1) from error
    raise SystemExit(returncode if returncode >= 0 else 128 - returncode)


if __name__ == "__main__":
    main()
