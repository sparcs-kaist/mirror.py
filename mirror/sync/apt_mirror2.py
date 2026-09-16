import mirror
import mirror.socket.worker
import mirror.structure
import mirror.sync

import ftplib
import html.parser
import importlib.util
import json
import logging
import os
import posixpath
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)
_MAX_METADATA_SIZE = 16 * 1024 * 1024
_MAX_DISTRIBUTIONS = 1024
_MAX_LISTING_LINKS = 4096
_OPERATION_TIMEOUT = 30
_ENTRY_TIMEOUT = 300
_DIST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]*$")
_ARCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_FIELD_RE = re.compile(rb"^([A-Za-z0-9-]+):[ \t]*(.*)$")
_INDEX_RE = re.compile(r"^(?:Packages|Sources)(?:\.(?:gz|xz|bz2))?$")
_ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None


class AptMirrorError(RuntimeError):
    """Raised when apt-mirror2 input or repository metadata is unsafe."""


class ResourceMissing(AptMirrorError):
    """Raised when an optional remote resource does not exist."""


@dataclass(frozen=True)
class RepositoryRequest:
    src: str
    dst: str
    dist: tuple[str, ...] | None
    section: tuple[str, ...] | None
    arch: tuple[str, ...] | None
    source: bool
    check_gpg: bool
    keyrings: tuple[str, ...]
    deadline: float


@dataclass(frozen=True)
class ReleaseMetadata:
    dist: str
    components: tuple[str, ...]
    architectures: tuple[str, ...]
    index_paths: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedRepository:
    src: str
    dst: str
    dist: tuple[str, ...]
    section: tuple[str, ...]
    arch: tuple[str, ...]
    source: bool
    binaries: bool
    flat: bool
    check_gpg: bool
    keyrings: tuple[str, ...]


class _ListingParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value is not None:
                if len(self.hrefs) >= _MAX_LISTING_LINKS:
                    raise AptMirrorError(
                        "repository listing has too many links; set dist explicitly"
                    )
                self.hrefs.append(value)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
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
        if new.scheme not in {"http", "https"}:
            raise AptMirrorError("repository redirect uses an unsupported scheme")
        if old.scheme == "https" and new.scheme != "https":
            raise AptMirrorError("repository redirect would downgrade HTTPS")
        if new.username or new.password:
            raise AptMirrorError("repository redirect introduces URL credentials")
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )


def setup(path: Path, package: mirror.structure.Package) -> None:
    """Prepare the sync environment (no-op for apt-mirror2)."""
    pass


def _get_log_path(logger: logging.Logger) -> str | None:
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return handler.baseFilename
    return None


def execute(
    package: mirror.structure.Package,
    logger: logging.Logger,
    trigger: str = "auto",
) -> None:
    """Validate and delegate an apt-mirror2 job to the worker.

    Args:
        package(mirror.structure.Package): Package to synchronize.
        logger(logging.Logger): Logger for the sync session.
        trigger(str): Accepted for sync interface compatibility.
    """
    del trigger
    logger.info(f"Starting sync.apt-mirror2 for {package.name}")
    try:
        payload = build_payload(package)
        if importlib.util.find_spec("apt_mirror") is None:
            raise AptMirrorError("apt-mirror2 Python module is not installed")
        command = [
            sys.executable,
            "-c",
            "from mirror.sync.apt_mirror2 import main; main()",
            "--",
            json.dumps(payload, separators=(",", ":")),
        ]
        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="apt-mirror2",
            commandline=command,
            env=dict(mirror.sync.get_extra_args(package.pkgid)),
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=_get_log_path(logger),
        )
    except Exception as error:
        logger.error(f"Sync for {package.pkgid} failed: {error}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def _reject_control(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"apt-mirror2 {label}: must be a string")
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"apt-mirror2 {label}: must be non-empty and contain no controls")
    return value


def _validate_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("apt-mirror2 src: must be a string")
    src = _reject_control(value, "src")
    if any(character.isspace() for character in src) or any(
        character in src for character in ("$", "#")
    ):
        raise ValueError("apt-mirror2 src: whitespace, '$', and '#' are not supported")
    parsed = urllib.parse.urlsplit(src)
    if parsed.scheme not in {"http", "https", "ftp"}:
        raise ValueError("apt-mirror2 src: scheme must be http, https, or ftp")
    if parsed.username or parsed.password:
        raise ValueError("apt-mirror2 src: URL credentials are not supported")
    if not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("apt-mirror2 src: host is required; query and fragment are forbidden")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("apt-mirror2 src: invalid port") from error
    decoded_path = urllib.parse.unquote(parsed.path)
    if any(part == ".." for part in PurePosixPath(decoded_path).parts):
        raise ValueError("apt-mirror2 src: path traversal is forbidden")
    return src.rstrip("/") + "/"


def _canonical_url(src: str) -> str:
    parsed = urllib.parse.urlsplit(src)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else 21
    port = f":{parsed.port}" if parsed.port and parsed.port != default_port else ""
    decoded_path = urllib.parse.unquote(parsed.path)
    normalized_path = posixpath.normpath(decoded_path)
    if decoded_path.endswith("/") and not normalized_path.endswith("/"):
        normalized_path += "/"
    path = urllib.parse.quote(normalized_path, safe="/~:@!&'()*+,;=-._")
    return urllib.parse.urlunsplit((scheme, hostname + port, path, "", ""))


def _validate_global_dst(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("apt-mirror2 dst: must be a string")
    dst = _reject_control(value, "dst")
    path = Path(dst)
    forbidden = any(character.isspace() or character in "$#" for character in dst)
    if not path.is_absolute() or path == Path("/") or forbidden:
        raise ValueError(
            "apt-mirror2 dst: must be a non-root absolute path without whitespace, '$', or '#'"
        )
    return os.path.normpath(dst)


def _validate_relative_dst(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("apt-mirror2 config dst: must be a string")
    dst = _reject_control(value, "config dst")
    if any(character.isspace() or character in "$#" for character in dst) or "\\" in dst:
        raise ValueError(
            "apt-mirror2 config dst: whitespace, '$', '#', and backslashes are forbidden"
        )
    path = PurePosixPath(dst)
    if (
        path.is_absolute()
        or dst in {".", "./"}
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("apt-mirror2 config dst: must be a safe non-empty relative path")
    return path.as_posix().rstrip("/")


def _parse_selection(
    value: object,
    label: str,
    pattern: re.Pattern[str],
    allow_flat: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError(f"apt-mirror2 {label}: must be a string or list of strings")
    if not values:
        raise ValueError(f"apt-mirror2 {label}: must not be empty")
    result: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item:
            raise ValueError(f"apt-mirror2 {label}: entries must be non-empty strings")
        _reject_control(item, label)
        valid_flat = allow_flat and item.endswith("/") and _safe_flat_directory(item)
        if not valid_flat and pattern.fullmatch(item) is None:
            raise ValueError(f"apt-mirror2 {label}: invalid value {item!r}")
        if item not in result:
            result.append(item)
    return tuple(result)


def _safe_flat_directory(value: str) -> bool:
    if value == "./":
        return True
    path = PurePosixPath(value.rstrip("/"))
    return (
        not path.is_absolute()
        and bool(path.parts)
        and path.as_posix() == value.rstrip("/")
        and all(part not in {"", ".", ".."} and _SECTION_RE.fullmatch(part) for part in path.parts)
    )


def _safe_component(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and path.as_posix() == value
        and all(
            part not in {"", ".", ".."} and _ARCH_RE.fullmatch(part)
            for part in path.parts
        )
    )


def _keyrings(value: object, check_gpg: bool) -> tuple[str, ...]:
    if value is None:
        values: list[object] = []
    elif isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("apt-mirror2 keyring: must be a string or list of strings")
    result: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError("apt-mirror2 keyring: entries must be strings")
        path = _reject_control(item, "keyring")
        forbidden = any(character.isspace() or character in "$#,[]=" for character in path)
        if not Path(path).is_absolute() or forbidden:
            raise ValueError(
                "apt-mirror2 keyring: must be an absolute path without native delimiters"
            )
        if path not in result:
            result.append(path)
    if check_gpg and not result:
        raise ValueError("apt-mirror2 keyring: required when check_gpg is enabled")
    return tuple(result)


def _validate_rate(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("apt-mirror2 limit_rate: must be a positive size")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("apt-mirror2 limit_rate: must be positive")
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*[kKmM]?", value):
        return value.lower()
    raise ValueError("apt-mirror2 limit_rate: use positive bytes/s with optional k or m")


def _validate_paths_do_not_overlap(destinations: list[str]) -> None:
    paths = [PurePosixPath(item) for item in destinations]
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError("apt-mirror2 config dst paths must not overlap")


def _build_payload(global_dst: object, options: object) -> dict[str, object]:
    if not isinstance(options, dict):
        raise ValueError("apt-mirror2 options must be an object")
    allowed = {"config", "source", "nthreads", "limit_rate"}
    unknown = set(options) - allowed
    if unknown:
        raise ValueError(f"apt-mirror2 unknown options: {sorted(unknown)}")
    config = options.get("config")
    if not isinstance(config, list) or not config:
        raise ValueError("apt-mirror2 options.config must be a non-empty list")
    source_default = options.get("source", False)
    if not isinstance(source_default, bool):
        raise ValueError("apt-mirror2 source must be boolean")
    nthreads = options.get("nthreads", 8)
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or nthreads <= 0:
        raise ValueError("apt-mirror2 nthreads must be a positive integer")
    limit_rate = _validate_rate(options.get("limit_rate"))

    repositories: list[dict[str, object]] = []
    sources: set[str] = set()
    destinations: list[str] = []
    allowed_item = {
        "src", "dst", "dist", "section", "arch", "source", "check_gpg", "keyring"
    }
    for raw in config:
        if not isinstance(raw, dict):
            raise ValueError("apt-mirror2 config entries must be objects")
        unknown_item = set(raw) - allowed_item
        if unknown_item:
            raise ValueError(f"apt-mirror2 unknown config options: {sorted(unknown_item)}")
        src = _validate_url(raw.get("src"))
        normalized_src = _canonical_url(src)
        if normalized_src in sources:
            raise ValueError("apt-mirror2 config src values must be unique")
        sources.add(normalized_src)
        repository_dst = _validate_relative_dst(raw.get("dst"))
        destinations.append(repository_dst)
        item_source = raw.get("source", source_default)
        check_gpg = raw.get("check_gpg", True)
        if not isinstance(item_source, bool) or not isinstance(check_gpg, bool):
            raise ValueError("apt-mirror2 source and check_gpg must be boolean")
        repository: dict[str, object] = {
            "src": src,
            "dst": repository_dst,
            "source": item_source,
            "check_gpg": check_gpg,
            "keyring": list(_keyrings(raw.get("keyring"), check_gpg)),
        }
        if "dist" in raw:
            repository["dist"] = list(_parse_selection(raw["dist"], "dist", _DIST_RE, True))
        if "section" in raw:
            sections = _parse_selection(raw["section"], "section", _SECTION_RE)
            if not all(_safe_component(section) for section in sections):
                raise ValueError("apt-mirror2 section: path traversal is forbidden")
            repository["section"] = list(sections)
        if "arch" in raw:
            arches = _parse_selection(raw["arch"], "arch", _ARCH_RE)
            if "source" in arches:
                raise ValueError(
                    "apt-mirror2 arch: use source=true instead of the source token"
                )
            repository["arch"] = list(arches)
        repositories.append(repository)
    _validate_paths_do_not_overlap(destinations)
    return {
        "dst": _validate_global_dst(global_dst),
        "nthreads": nthreads,
        "limit_rate": limit_rate,
        "repositories": repositories,
    }


def build_payload(package: mirror.structure.Package) -> dict[str, object]:
    """Build a JSON-safe worker payload without filesystem or network access.

    Args:
        package(mirror.structure.Package): Package containing apt-mirror2 options.

    Return:
        payload(dict): Validated worker input.
    """
    return _build_payload(package.settings.dst, package.settings.options)


def _validate_worker_payload(payload: dict[str, object]) -> dict[str, object]:
    allowed = {"dst", "nthreads", "limit_rate", "repositories"}
    if set(payload) != allowed:
        raise AptMirrorError("worker payload has unexpected fields")
    options = {
        "config": payload["repositories"],
        "nthreads": payload["nthreads"],
        "limit_rate": payload["limit_rate"],
    }
    try:
        return _build_payload(payload["dst"], options)
    except ValueError as error:
        raise AptMirrorError(str(error)) from error


def _remaining(request: RepositoryRequest) -> float:
    remaining = request.deadline - time.monotonic()
    if remaining <= 0:
        raise AptMirrorError("repository discovery exceeded 300 seconds")
    return min(_OPERATION_TIMEOUT, remaining)


def _resource_url(request: RepositoryRequest, relative: str) -> str:
    if any(part == ".." for part in PurePosixPath(relative).parts):
        raise AptMirrorError("repository metadata path escapes its root")
    return urllib.parse.urljoin(request.src, relative)


def _bounded_read(response: Any, request: RepositoryRequest) -> bytes:
    chunks: list[bytes] = []
    size = 0
    read_chunk = getattr(response, "read1", response.read)
    while True:
        _remaining(request)
        chunk = read_chunk(min(64 * 1024, _MAX_METADATA_SIZE + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > _MAX_METADATA_SIZE:
            raise AptMirrorError("repository metadata exceeds the 16 MiB limit")


def _http_fetch(request: RepositoryRequest, relative: str) -> bytes:
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    try:
        with opener.open(_resource_url(request, relative), timeout=_remaining(request)) as response:
            return _bounded_read(response, request)
    except urllib.error.HTTPError as error:
        if error.code in {404, 410}:
            raise ResourceMissing("repository resource is missing") from error
        raise AptMirrorError(f"repository HTTP request failed with status {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AptMirrorError("repository HTTP request failed") from error


def _ftp_connection(request: RepositoryRequest) -> ftplib.FTP:
    parsed = urllib.parse.urlsplit(request.src)
    try:
        ftp = ftplib.FTP()
        ftp.connect(parsed.hostname or "", parsed.port or 21, timeout=_remaining(request))
        ftp.login(
            urllib.parse.unquote(parsed.username) if parsed.username else "anonymous",
            urllib.parse.unquote(parsed.password) if parsed.password else "anonymous@",
        )
        return ftp
    except (OSError, ftplib.Error) as error:
        raise AptMirrorError("repository FTP connection failed") from error


def _ftp_resource_path(request: RepositoryRequest, relative: str) -> str:
    root = urllib.parse.unquote(urllib.parse.urlsplit(request.src).path).strip("/")
    return "/" + "/".join(filter(None, (root, relative)))


def _ftp_fetch(request: RepositoryRequest, relative: str) -> bytes:
    ftp = _ftp_connection(request)
    chunks: list[bytes] = []
    size = 0

    def append(chunk: bytes) -> None:
        nonlocal size
        _remaining(request)
        size += len(chunk)
        if size > _MAX_METADATA_SIZE:
            raise AptMirrorError("repository metadata exceeds the 16 MiB limit")
        chunks.append(chunk)

    try:
        ftp.retrbinary(f"RETR {_ftp_resource_path(request, relative)}", append)
        return b"".join(chunks)
    except ftplib.error_perm as error:
        if str(error).startswith("550") and relative.endswith("InRelease"):
            raise ResourceMissing("repository InRelease is missing") from error
        raise AptMirrorError("repository FTP request failed") from error
    except (OSError, ftplib.Error) as error:
        raise AptMirrorError("repository FTP request failed") from error
    finally:
        try:
            ftp.quit()
        except (OSError, ftplib.Error):
            ftp.close()


def _fetch(request: RepositoryRequest, relative: str) -> bytes:
    scheme = urllib.parse.urlsplit(request.src).scheme
    if scheme == "ftp":
        return _ftp_fetch(request, relative)
    return _http_fetch(request, relative)


def _http_list_distributions(request: RepositoryRequest) -> tuple[str, ...]:
    listing_url = _resource_url(request, "dists/")
    parser = _ListingParser()
    try:
        parser.feed(_http_fetch(request, "dists/").decode("utf-8", "strict"))
    except UnicodeDecodeError as error:
        raise AptMirrorError("repository dists listing is not valid UTF-8") from error
    base = urllib.parse.urlsplit(listing_url)
    base_path = urllib.parse.unquote(base.path).rstrip("/") + "/"
    names: set[str] = set()
    for href in parser.hrefs:
        target = urllib.parse.urlsplit(urllib.parse.urljoin(listing_url, href))
        target_path = urllib.parse.unquote(target.path)
        if target.scheme != base.scheme or target.netloc != base.netloc:
            continue
        if target.query or target.fragment or not target_path.endswith("/"):
            continue
        if not target_path.startswith(base_path):
            continue
        remainder = target_path[len(base_path):].strip("/")
        if "/" not in remainder and _DIST_RE.fullmatch(remainder):
            names.add(remainder)
    return tuple(sorted(names))


def _stream_ftp_listing(
    ftp: ftplib.FTP,
    path: str,
    request: RepositoryRequest,
    consume: Callable[[str, dict[str, str]], None],
) -> None:
    entry_count = 0

    def consume_mlsd_line(line: str) -> None:
        nonlocal entry_count
        entry_count += 1
        _remaining(request)
        if entry_count > _MAX_LISTING_LINKS:
            raise AptMirrorError("repository FTP listing has too many entries")
        fact_text, separator, name = line.partition(" ")
        if not separator:
            return
        facts: dict[str, str] = {}
        for fact in fact_text.rstrip(";").split(";"):
            key, equals, value = fact.partition("=")
            if equals:
                facts[key.lower()] = value
        consume(name, facts)

    try:
        _receive_ftp_listing(ftp, f"MLSD {path}", request, consume_mlsd_line)
        return
    except ftplib.error_perm as error:
        if not str(error).startswith(("500", "501", "502", "504")):
            raise

    entry_count = 0

    def consume_nlst_line(line: str) -> None:
        nonlocal entry_count
        entry_count += 1
        _remaining(request)
        if entry_count > _MAX_LISTING_LINKS:
            raise AptMirrorError("repository FTP listing has too many entries")
        consume(PurePosixPath(line.rstrip("/")).name, {})

    _receive_ftp_listing(ftp, f"NLST {path}", request, consume_nlst_line)


def _receive_ftp_listing(
    ftp: ftplib.FTP,
    command: str,
    request: RepositoryRequest,
    consume_line: Callable[[str], None],
) -> None:
    connection = ftp.transfercmd(command)
    buffer = bytearray()
    received = 0
    completed = False
    try:
        while True:
            connection.settimeout(_remaining(request))
            chunk = connection.recv(64 * 1024)
            _remaining(request)
            if not chunk:
                completed = True
                break
            received += len(chunk)
            if received > _MAX_METADATA_SIZE:
                raise AptMirrorError("repository FTP listing exceeds the 16 MiB limit")
            buffer.extend(chunk)
            while b"\n" in buffer:
                raw_line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                consume_line(raw_line.rstrip(b"\r").decode(ftp.encoding, "strict"))
        if buffer:
            consume_line(bytes(buffer).rstrip(b"\r").decode(ftp.encoding, "strict"))
    except UnicodeDecodeError as error:
        raise AptMirrorError("repository FTP listing is not valid text") from error
    except OSError as error:
        raise AptMirrorError("repository FTP listing failed") from error
    finally:
        connection.close()
    if completed:
        if ftp.sock is not None:
            ftp.sock.settimeout(_remaining(request))
        ftp.voidresp()


def _ftp_list_distributions(request: RepositoryRequest) -> tuple[str, ...]:
    ftp = _ftp_connection(request)
    path = _ftp_resource_path(request, "dists")
    names: set[str] = set()

    def add_name(value: str, facts: dict[str, str]) -> None:
        if facts and facts.get("type") != "dir":
            return
        name = PurePosixPath(value.rstrip("/")).name
        if _DIST_RE.fullmatch(name):
            names.add(name)
        if len(names) > _MAX_DISTRIBUTIONS:
            raise AptMirrorError(
                "repository has too many distributions; set dist explicitly"
            )

    try:
        _stream_ftp_listing(ftp, path, request, add_name)
        return tuple(sorted(names))
    except (OSError, ftplib.Error) as error:
        raise AptMirrorError("repository dists listing failed; set dist explicitly") from error
    finally:
        ftp.close()


def _ftp_root_has_release(request: RepositoryRequest) -> bool:
    ftp = _ftp_connection(request)
    path = _ftp_resource_path(request, "")
    names: set[str] = set()

    def add_name(value: str, facts: dict[str, str]) -> None:
        del facts
        names.add(PurePosixPath(value.rstrip("/")).name)

    try:
        _stream_ftp_listing(ftp, path, request, add_name)
        return bool(names & {"InRelease", "Release"})
    except (OSError, ftplib.Error) as error:
        raise AptMirrorError("repository FTP root listing failed") from error
    finally:
        ftp.close()


def _list_distributions(request: RepositoryRequest) -> tuple[str, ...]:
    if urllib.parse.urlsplit(request.src).scheme == "ftp":
        names = _ftp_list_distributions(request)
    else:
        names = _http_list_distributions(request)
    if not names:
        raise AptMirrorError("no distributions found; set dist explicitly")
    if len(names) > _MAX_DISTRIBUTIONS:
        raise AptMirrorError("too many distributions found; set dist explicitly")
    return names


def _run_gpg(command: list[str], request: RepositoryRequest) -> None:
    global _ACTIVE_PROCESS
    try:
        _ACTIVE_PROCESS = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        _ACTIVE_PROCESS.communicate(timeout=_remaining(request))
        if _ACTIVE_PROCESS.returncode != 0:
            raise AptMirrorError("Release signature verification failed")
    except subprocess.TimeoutExpired as error:
        assert _ACTIVE_PROCESS is not None
        _ACTIVE_PROCESS.kill()
        _ACTIVE_PROCESS.communicate()
        raise AptMirrorError("Release signature verification timed out") from error
    except OSError as error:
        raise AptMirrorError("gpgv could not be started") from error
    finally:
        _ACTIVE_PROCESS = None


def _extract_inrelease(data: bytes) -> bytes:
    lines = data.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != b"-----BEGIN PGP SIGNED MESSAGE-----":
        raise AptMirrorError("repository InRelease is not a clearsigned message")
    index = 1
    while index < len(lines) and lines[index].rstrip(b"\r\n"):
        index += 1
    index += 1
    content: list[bytes] = []
    while index < len(lines):
        line = lines[index]
        if line.rstrip(b"\r\n") == b"-----BEGIN PGP SIGNATURE-----":
            if not content:
                raise AptMirrorError("repository InRelease has no signed content")
            return b"".join(item[2:] if item.startswith(b"- ") else item for item in content)
        content.append(line)
        index += 1
    raise AptMirrorError("repository InRelease has no signature")


def _verify_release(
    request: RepositoryRequest,
    release: bytes,
    signature: bytes | None,
) -> bytes:
    if not request.check_gpg:
        return _extract_inrelease(release) if signature is None else release
    for keyring in request.keyrings:
        path = Path(keyring)
        if not path.is_file() or not os.access(path, os.R_OK):
            raise AptMirrorError(f"configured keyring is not readable: {keyring}")
    with tempfile.TemporaryDirectory(prefix="mirror-apt-mirror2-gpg-") as directory:
        release_path = Path(directory) / "Release"
        release_path.write_bytes(release)
        command = ["gpgv"]
        for keyring in request.keyrings:
            command.extend(["--keyring", keyring])
        if signature is None:
            output = Path(directory) / "verified"
            command.extend(["--output", str(output), str(release_path)])
            _run_gpg(command, request)
            return output.read_bytes()
        signature_path = Path(directory) / "Release.gpg"
        signature_path.write_bytes(signature)
        command.extend([str(signature_path), str(release_path)])
        _run_gpg(command, request)
        return release


def _read_release(request: RepositoryRequest, prefix: str) -> bytes:
    relative = f"{prefix.rstrip('/')}/InRelease".lstrip("/")
    try:
        inrelease = _fetch(request, relative)
    except ResourceMissing:
        release_name = f"{prefix.rstrip('/')}/Release".lstrip("/")
        release = _fetch(request, release_name)
        if not request.check_gpg:
            return release
        try:
            signature = _fetch(request, f"{release_name}.gpg")
        except ResourceMissing as error:
            raise AptMirrorError(
                f"repository {release_name} is missing Release.gpg"
            ) from error
        return _verify_release(request, release, signature)
    return _verify_release(request, inrelease, None)


def _parse_release(dist: str, data: bytes) -> ReleaseMetadata:
    wanted = {"Components", "Architectures", "SHA512", "SHA256", "SHA1", "MD5Sum"}
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in data.splitlines():
        if line.startswith((b" ", b"\t")):
            if current in wanted:
                fields[current].append(line.strip().decode("utf-8", "strict"))
            continue
        match = _FIELD_RE.match(line)
        if match is None:
            current = None
            continue
        current = match.group(1).decode("ascii")
        if current not in wanted:
            continue
        if current in fields:
            raise AptMirrorError(f"Release contains duplicate {current} field")
        fields[current] = [match.group(2).decode("utf-8", "strict").strip()]
    index_paths: list[str] = []
    for name in ("SHA512", "SHA256", "SHA1", "MD5Sum"):
        for line in fields.get(name, []):
            parts = line.split()
            if len(parts) == 3 and parts[1].isdigit():
                index_paths.append(parts[2])
    return ReleaseMetadata(
        dist=dist,
        components=tuple(" ".join(fields.get("Components", [])).split()),
        architectures=tuple(" ".join(fields.get("Architectures", [])).split()),
        index_paths=tuple(index_paths),
    )


def _root_is_flat(metadata: ReleaseMetadata, source: bool) -> bool:
    has_binary, has_source = _flat_index_types(metadata)
    return has_binary or (source and has_source)


def _flat_index_types(metadata: ReleaseMetadata) -> tuple[bool, bool]:
    names = {PurePosixPath(path).name for path in metadata.index_paths if "/" not in path}
    has_binary = any(name.startswith("Packages") and _INDEX_RE.fullmatch(name) for name in names)
    has_source = any(name.startswith("Sources") and _INDEX_RE.fullmatch(name) for name in names)
    return has_binary, has_source


def _request_from_dict(raw: dict[str, object]) -> RepositoryRequest:
    dist = raw.get("dist")
    section = raw.get("section")
    arch = raw.get("arch")
    keyrings = raw["keyring"]
    if dist is not None and not isinstance(dist, list):
        raise AptMirrorError("worker dist must be a list")
    if section is not None and not isinstance(section, list):
        raise AptMirrorError("worker section must be a list")
    if arch is not None and not isinstance(arch, list):
        raise AptMirrorError("worker arch must be a list")
    if not isinstance(keyrings, list):
        raise AptMirrorError("worker keyring must be a list")
    return RepositoryRequest(
        src=str(raw["src"]),
        dst=str(raw["dst"]),
        dist=tuple(dist) if dist is not None else None,
        section=tuple(section) if section is not None else None,
        arch=tuple(arch) if arch is not None else None,
        source=bool(raw["source"]),
        check_gpg=bool(raw["check_gpg"]),
        keyrings=tuple(keyrings),
        deadline=time.monotonic() + _ENTRY_TIMEOUT,
    )


def _guard_distribution_shrink(destination: Path, selected: tuple[str, ...]) -> None:
    dists = destination / "dists"
    if not dists.is_dir():
        return
    existing = {
        entry.name
        for entry in dists.iterdir()
        if entry.is_dir() and not entry.is_symlink()
    }
    missing = existing - set(selected)
    if missing:
        raise AptMirrorError(
            "automatic discovery would remove existing distributions: " + ", ".join(sorted(missing))
        )


def _validate_destination_paths(destination: Path, repositories: list[dict[str, object]]) -> None:
    base = destination.resolve()
    resolved_paths: list[Path] = []
    for repository in repositories:
        current = destination
        for part in PurePosixPath(str(repository["dst"])).parts:
            current /= part
            if current.is_symlink():
                raise AptMirrorError(f"repository destination contains a symlink: {current}")
        resolved = current.resolve(strict=False)
        if not resolved.is_relative_to(base):
            raise AptMirrorError("repository destination escapes the package destination")
        for other in resolved_paths:
            paths_overlap = (
                resolved == other
                or resolved.is_relative_to(other)
                or other.is_relative_to(resolved)
            )
            if paths_overlap:
                raise AptMirrorError("repository destination paths overlap after resolution")
        resolved_paths.append(resolved)


def resolve_repository(raw: dict[str, object], global_dst: Path) -> ResolvedRepository:
    """Resolve omitted selections for one repository config item.

    Args:
        raw(dict): Validated repository payload.
        global_dst(Path): Package destination used by the shrink guard.

    Return:
        repository(ResolvedRepository): Native apt-mirror2 selections.
    """
    request = _request_from_dict(raw)
    explicit_dist = request.dist is not None
    dist = request.dist
    flat = bool(dist and any(item.endswith("/") for item in dist))
    if flat and dist and not all(item.endswith("/") for item in dist):
        raise AptMirrorError("flat and normal distributions cannot be mixed")

    flat_metadata: list[ReleaseMetadata] = []
    if dist is None:
        root_metadata = None
        scheme = urllib.parse.urlsplit(request.src).scheme
        should_probe_root = scheme != "ftp" or _ftp_root_has_release(request)
        if should_probe_root:
            try:
                root_metadata = _parse_release("./", _read_release(request, ""))
            except ResourceMissing:
                root_metadata = None
        if root_metadata is not None and _root_is_flat(root_metadata, request.source):
            dist = ("./",)
            flat = True
            flat_metadata = [root_metadata]
        else:
            dist = _list_distributions(request)

    if flat:
        if request.section is not None or request.arch is not None:
            raise AptMirrorError("flat repositories do not accept section or arch")
        if not flat_metadata:
            for directory in dist:
                prefix = directory.rstrip("/")
                if prefix == ".":
                    prefix = ""
                try:
                    data = _read_release(request, prefix)
                except ResourceMissing as error:
                    raise AptMirrorError(
                        "apt-mirror2 v16 requires Release metadata even when "
                        "check_gpg is false"
                    ) from error
                flat_metadata.append(_parse_release(directory, data))
        index_types = [_flat_index_types(item) for item in flat_metadata]
        binary_values = {item[0] for item in index_types}
        source_values = {item[1] for item in index_types}
        if len(binary_values) > 1 or (request.source and len(source_values) > 1):
            raise AptMirrorError(
                "selected flat directories must expose the same binary and source index types"
            )
        flat_binaries = binary_values == {True}
        has_sources = source_values == {True}
        if not flat_binaries and not (request.source and has_sources):
            raise AptMirrorError("flat Release metadata has no requested package indexes")
        if request.source and flat_binaries and not has_sources:
            raise AptMirrorError("flat Release metadata has no Sources index")
        return ResolvedRepository(
            src=request.src,
            dst=request.dst,
            dist=dist,
            section=(),
            arch=("all",) if flat_binaries else (),
            source=request.source,
            binaries=flat_binaries,
            flat=True,
            check_gpg=request.check_gpg,
            keyrings=request.keyrings,
        )

    metadata: list[ReleaseMetadata] = []
    for name in dist:
        try:
            metadata.append(_parse_release(name, _read_release(request, f"dists/{name}")))
        except ResourceMissing:
            if explicit_dist:
                raise AptMirrorError(f"distribution {name} has no Release metadata")
    if not metadata:
        raise AptMirrorError("no valid distributions found")
    valid_dist = tuple(item.dist for item in metadata)
    if not explicit_dist:
        _guard_distribution_shrink(global_dst / request.dst, valid_dist)
    sections = request.section
    if sections is None:
        missing_components = [item.dist for item in metadata if not item.components]
        if missing_components:
            raise AptMirrorError(
                "Release metadata has no Components for: " + ", ".join(missing_components)
            )
        sections = tuple(sorted({value for item in metadata for value in item.components}))
        invalid_components = [value for value in sections if not _safe_component(value)]
        if invalid_components:
            raise AptMirrorError(
                "Release metadata contains unsafe Components: "
                + ", ".join(invalid_components)
            )
    arches = request.arch
    if arches is None:
        with_architectures = [item.dist for item in metadata if item.architectures]
        without_architectures = [item.dist for item in metadata if not item.architectures]
        if without_architectures and (not request.source or with_architectures):
            raise AptMirrorError(
                "Release metadata has no Architectures for: "
                + ", ".join(without_architectures)
            )
        arches = tuple(
            sorted(
                {
                    value
                    for item in metadata
                    for value in item.architectures
                    if value != "source"
                }
            )
        )
        invalid_arches = [value for value in arches if _ARCH_RE.fullmatch(value) is None]
        if invalid_arches:
            raise AptMirrorError(
                "Release metadata contains unsafe Architectures: "
                + ", ".join(invalid_arches)
            )
    binaries = bool(arches)
    if not binaries and not request.source:
        raise AptMirrorError("Release metadata has no binary architectures")
    return ResolvedRepository(
        src=request.src,
        dst=request.dst,
        dist=valid_dist,
        section=sections,
        arch=arches,
        source=request.source,
        binaries=binaries,
        flat=False,
        check_gpg=request.check_gpg,
        keyrings=request.keyrings,
    )


def _build_source_options(repository: ResolvedRepository, include_arch: bool) -> str:
    options: list[str] = []
    if include_arch:
        options.append("arch=" + ",".join(repository.arch))
    if repository.keyrings:
        options.append("signed-by=" + ",".join(repository.keyrings))
    return f"[{' '.join(options)}] " if options else ""


def render_config(
    payload: dict[str, object],
    repositories: list[ResolvedRepository],
    runtime: Path,
) -> str:
    """Render an isolated apt-mirror2 configuration.

    Args:
        payload(dict): Validated package settings.
        repositories(list[ResolvedRepository]): Discovered repositories.
        runtime(Path): Private runtime directory.

    Return:
        config(str): Complete native configuration text.
    """
    destination = str(payload["dst"])
    lines = [
        f"set base_path {runtime}",
        f"set mirror_path {destination}",
        f"set skel_path {runtime / 'skel'}",
        f"set var_path {runtime / 'var'}",
        f"set nthreads {payload['nthreads']}",
        f"set limit_rate {payload['limit_rate'] or '0'}",
        "set _autoclean 1",
        "set wipe_size_ratio 0.4",
        "set wipe_count_ratio 0.4",
        "set gpg_verify off",
        "set run_postmirror 0",
        "set uvloop 0",
    ]
    if payload["limit_rate"] is not None:
        lines.append("set slow_rate_protection off")
    for repository in repositories:
        dist = ",".join(repository.dist)
        components = " " + " ".join(repository.section) if repository.section else ""
        if repository.binaries:
            lines.append(
                f"deb {_build_source_options(repository, True)}{repository.src} {dist}{components}"
            )
        if repository.source:
            source_options = _build_source_options(repository, False)
            lines.append(
                f"deb-src {source_options}{repository.src} {dist}{components}"
            )
        lines.extend(
            [
                f"mirror_path {repository.src} {repository.dst}",
                f"gpg_verify {repository.src} {'force' if repository.check_gpg else 'off'}",
                f"clean {repository.src}",
            ]
        )
    return "\n".join(lines) + "\n"


def _terminate_child(signum: int, frame: object) -> None:
    global _ACTIVE_PROCESS
    del frame
    if _ACTIVE_PROCESS is not None:
        _ACTIVE_PROCESS.terminate()
        try:
            _ACTIVE_PROCESS.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _ACTIVE_PROCESS.kill()
            _ACTIVE_PROCESS.wait()
    raise SystemExit(128 + signum)


def run_payload(payload: dict[str, object]) -> int:
    """Discover repositories, generate temporary config, and run apt-mirror2.

    Args:
        payload(dict): JSON-decoded worker payload from build_payload().

    Return:
        returncode(int): Native apt-mirror2 process return code.
    """
    global _ACTIVE_PROCESS
    payload = _validate_worker_payload(payload)
    destination = Path(str(payload["dst"]))
    destination.mkdir(parents=True, exist_ok=True)
    raw_repositories = payload["repositories"]
    if not isinstance(raw_repositories, list) or not all(
        isinstance(repository, dict) for repository in raw_repositories
    ):
        raise AptMirrorError("worker repositories must be a list of objects")
    _validate_destination_paths(destination, raw_repositories)
    repositories = [
        resolve_repository(raw, destination)
        for raw in raw_repositories
    ]
    with tempfile.TemporaryDirectory(prefix=".mirror-apt-mirror2-", dir=destination) as directory:
        runtime = Path(directory)
        runtime.chmod(0o700)
        config = runtime / "mirror.list"
        config.write_text(render_config(payload, repositories, runtime), encoding="utf-8")
        config.chmod(0o600)
        try:
            _ACTIVE_PROCESS = subprocess.Popen([sys.executable, "-m", "apt_mirror", str(config)])
            return _ACTIVE_PROCESS.wait()
        except OSError as error:
            raise AptMirrorError("apt-mirror2 could not be started") from error
        finally:
            _ACTIVE_PROCESS = None


def main() -> None:
    """Worker entry point for apt-mirror2 discovery and execution."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    signal.signal(signal.SIGTERM, _terminate_child)
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if len(arguments) != 1:
        _LOGGER.error("apt-mirror2 wrapper requires one JSON payload")
        raise SystemExit(2)
    try:
        payload = json.loads(arguments[0])
        if not isinstance(payload, dict):
            raise AptMirrorError("worker payload must be an object")
        returncode = run_payload(payload)
    except (AptMirrorError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        _LOGGER.error("apt-mirror2 failed: %s", error)
        raise SystemExit(1) from error
    raise SystemExit(returncode if returncode >= 0 else 128 - returncode)


def plugin() -> "mirror.plugin.PluginRecord":
    """Return the apt-mirror2 sync plugin record."""
    from mirror.plugin import sync_plugin

    return sync_plugin(name="apt-mirror2", execute=execute)


if __name__ == "__main__":
    main()
