import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit


_UNSAFE_LFTP_CHARS = set(";!`'\"$|&<>(){}[]\\*?~#")
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_DEFAULT_EXCLUDE_X = [r"\.in\..*\."]
_DEFAULT_EXCLUDE_X_UPPER = [r"\.(mirror|notar)", "lost+found"]


def _has_control_char(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _validate_host(host: str) -> None:
    if not host or len(host) > 253 or not host.isascii():
        raise ValueError("Invalid lftp source")

    labels = host.split(".")
    if any(not label or not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError("Invalid lftp source")


def _validate_url_path(path: str) -> None:
    if path in {"", "/"}:
        return

    parts = path.split("/")
    for index, part in enumerate(parts):
        is_first = index == 0
        is_last = index == len(parts) - 1

        if part == "":
            if is_first or is_last:
                continue
            raise ValueError("Invalid lftp source")
        if part == "..":
            raise ValueError("Invalid lftp source")
        if part == "." and not is_last:
            raise ValueError("Invalid lftp source")


def _parse_lftp_src(src: str) -> tuple[str, str]:
    # Reject percent-encoding so validation is one-to-one with the raw URL text.
    if not src or "%" in src:
        raise ValueError("Invalid lftp source")
    if any(ch.isspace() for ch in src) or _has_control_char(src):
        raise ValueError("Invalid lftp source")
    if any(ch in _UNSAFE_LFTP_CHARS for ch in src):
        raise ValueError("Invalid lftp source")

    parsed = urlsplit(src)
    if parsed.scheme.lower() != "ftp":
        raise ValueError("Invalid lftp source")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Invalid lftp source")

    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid lftp source") from exc

    host = parsed.hostname or ""
    _validate_host(host)
    _validate_url_path(parsed.path)
    return src, host


def _validate_lftp_dst(dst: str) -> None:
    if not dst or "\x00" in dst or _has_control_char(dst):
        raise ValueError("Invalid lftp destination")
    if dst.startswith("-") or any(ch.isspace() for ch in dst):
        raise ValueError("Invalid lftp destination")
    if any(ch in _UNSAFE_LFTP_CHARS for ch in dst):
        raise ValueError("Invalid lftp destination")
    Path(dst)


def _quote_lftp_value(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid lftp option")
    if "'" in value or ";" in value or "\x00" in value or _has_control_char(value):
        raise ValueError("Invalid lftp option")
    return f"'{value}'"


def _validate_pattern_list(options: dict, key: str, default: list[str]) -> list[str]:
    if key not in options:
        return list(default)
    value = options[key]
    if not isinstance(value, list):
        raise ValueError("Invalid lftp option")
    for item in value:
        _quote_lftp_value(item)
    return list(value)


def _validate_int_option(options: dict, key: str, default: int, minimum: int, maximum: int) -> int:
    value = options.get(key, default)
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("Invalid lftp option")
    return value


def _validate_lftp_options(options: dict) -> dict:
    if not isinstance(options, dict):
        raise ValueError("Invalid lftp option")

    list_options = options.get("list_options")
    if list_options is not None and list_options != "-a":
        raise ValueError("Invalid lftp option")

    scan_all_first = options.get("scan_all_first", False)
    if not isinstance(scan_all_first, bool):
        raise ValueError("Invalid lftp option")

    exclude_x = _validate_pattern_list(options, "exclude_x", _DEFAULT_EXCLUDE_X)
    exclude_X = _validate_pattern_list(options, "exclude_X", _DEFAULT_EXCLUDE_X_UPPER)
    exclude = _validate_pattern_list(options, "exclude", [])

    return {
        "list_options": list_options,
        "scan_all_first": scan_all_first,
        "exclude_x": exclude_x,
        "exclude_X": exclude_X,
        "exclude": exclude,
        "custom_excludes": "exclude_x" in options or "exclude_X" in options,
        "max_retries": _validate_int_option(options, "max_retries", 3, 1, 100),
        "net_timeout": _validate_int_option(options, "net_timeout", 60, 1, 3600),
    }


def _exclude_args(options: dict) -> list[str]:
    if not options["custom_excludes"]:
        args = [
            f"-X {_quote_lftp_value(_DEFAULT_EXCLUDE_X_UPPER[0])}",
            f"-x {_quote_lftp_value(_DEFAULT_EXCLUDE_X[0])}",
            f"-X {_quote_lftp_value(_DEFAULT_EXCLUDE_X_UPPER[1])}",
        ]
    else:
        args = [f"-X {_quote_lftp_value(value)}" for value in options["exclude_X"]]
        args.extend(f"-x {_quote_lftp_value(value)}" for value in options["exclude_x"])

    args.extend(f"--exclude={_quote_lftp_value(value)}" for value in options["exclude"])
    return args


def _build_lftp_script(src: str, dst: str, options: dict) -> str:
    source_url, host = _parse_lftp_src(src)
    clean_options = _validate_lftp_options(options)

    settings = [
        f"set ftp:anon-pass mirror@{host}",
        "set cmd:verbose yes",
        f"set net:max-retries {clean_options['max_retries']}",
        f"set net:timeout {clean_options['net_timeout']}",
    ]
    if clean_options["list_options"]:
        settings.append(f"set list-options {clean_options['list_options']}")

    mirror_args = ["mirror", "--continue", "--delete", "--no-perms", "--verbose=3"]
    if clean_options["scan_all_first"]:
        mirror_args.append("--scan-all-first")
    mirror_args.extend(_exclude_args(clean_options))
    mirror_args.extend([source_url, dst])

    return "; ".join(settings + [" ".join(mirror_args)])


def execute(package: mirror.structure.Package, pkg_logger: logging.Logger, trigger: str = "auto"):
    """Run the lftp Sync method (CORE)

    Args:
        package(mirror.structure.Package): Package object
        pkg_logger(logging.Logger): Logger object for this sync session
    """
    pkg_logger.info(f"Starting sync.lftp for {package.name}")

    try:
        src = package.settings.src
        dst = package.settings.dst
        _validate_lftp_dst(dst)
        lftp_script = _build_lftp_script(src, dst, package.settings.options)

        command = ["lftp", "-c", lftp_script]

        log_path = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        pkg_logger.info(f"Delegating lftp sync to worker: {' '.join(command)}")
        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="lftp",
            commandline=command,
            env={},
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=log_path,
        )

    except Exception as e:
        pkg_logger.error(f"lftp sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def plugin():
    """Entry-point factory for the lftp plug-in."""
    from mirror.plugin import sync_plugin
    return sync_plugin(name="lftp", execute=execute)
