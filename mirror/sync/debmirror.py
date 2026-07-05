"""Thin CLI-flag-driven executor for the Debian `debmirror` tool.

Unlike `rsync`/`ftpsync`, debmirror takes no config file to generate: every
mirror-relevant option is assembled into a single argv list by
build_command() and delegated to the worker via
mirror.socket.worker.execute_command(). There is no temp dir and no
on_sync_done cleanup hook (nothing to clean up).

Note: debmirror auto-reads /etc/debmirror.conf and ~/.debmirror.conf when
present on the host, so behavior can vary by machine. The argv assembled by
build_command() is intentionally authoritative: every mirror-relevant option
(method, host, root, dist, arch, cleanup mode, GPG check) is always emitted
explicitly on the command line, so a stray host-level debmirror.conf cannot
silently override the configured sync target.
"""

import logging
import re
import urllib.parse
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


def _parse_src(src: str, opts: dict) -> tuple[str, str, str]:
    """Resolve (method, host, root) from the package src URL, with per-field option overrides.

    Args:
        src(str): Package source URL (e.g. "http://deb.debian.org/debian").
        opts(dict): Package sync options; "method"/"host"/"root" override the
            corresponding value parsed from src.

    Return:
        parsed(tuple[str, str, str]): Validated (method, host, root).

    Raises:
        ValueError: A part cannot be resolved from src or options, or fails validation.
    """
    parsed = urllib.parse.urlparse(src) if src else None

    method = opts.get("method") or (parsed.scheme if parsed else None)
    if not method:
        raise ValueError("debmirror method: could not be resolved from src or options.method")
    method = _validate_token(method, "method", _METHOD_TOKEN_RE)
    _validate_enum(method, _METHODS, "method")

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

    Pure builder: does not touch the network, filesystem, or worker.

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

    dist_opt = opts.get("dist")
    if not dist_opt:
        raise ValueError("debmirror dist: option is required")
    dist = _join_multi(dist_opt, "dist", _DIST_RE)

    argv = ["debmirror", "--verbose", "--method", method, "--host", host, "--root", root, "--dist", dist]

    section_opt = opts.get("section")
    if section_opt:
        argv += ["--section", _join_multi(section_opt, "section", _SECTION_RE)]

    arch_opt = opts.get("arch")
    if arch_opt:
        argv += ["--arch", _join_multi(arch_opt, "arch", _DIST_RE)]

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
