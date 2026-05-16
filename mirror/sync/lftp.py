import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
import logging
from pathlib import Path, PurePosixPath


_UNSAFE_LFTP_CHARS = set(";!`'\"$|&<>(){}[]\\*?~#")


def _has_control_char(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _validate_lftp_src(src: str) -> None:
    if not src or "://" in src or "@" in src:
        raise ValueError("Invalid lftp source")
    if any(ch.isspace() for ch in src) or _has_control_char(src):
        raise ValueError("Invalid lftp source")
    if any(ch in _UNSAFE_LFTP_CHARS for ch in src):
        raise ValueError("Invalid lftp source")

    parts = src.split("/", 1)
    host = parts[0]
    if not host or host in {".", ".."}:
        raise ValueError("Invalid lftp source")
    if len(parts) == 2:
        path = PurePosixPath(parts[1])
        if any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Invalid lftp source")


def _validate_lftp_dst(dst: str) -> None:
    if not dst or "\x00" in dst or _has_control_char(dst):
        raise ValueError("Invalid lftp destination")
    if dst.startswith("-") or any(ch.isspace() for ch in dst):
        raise ValueError("Invalid lftp destination")
    if any(ch in _UNSAFE_LFTP_CHARS for ch in dst):
        raise ValueError("Invalid lftp destination")
    Path(dst)


def execute(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """Run the lftp Sync method (CORE)

    Args:
        package(mirror.structure.Package): Package object
        pkg_logger(logging.Logger): Logger object for this sync session
    """
    pkg_logger.info(f"Starting sync.lftp for {package.name}")

    try:
        src = package.settings.src
        dst = package.settings.dst
        _validate_lftp_src(src)
        _validate_lftp_dst(dst)

        lftp_script = (
            f"set ftp:anon-pass mirror@{src}; "
            f"set cmd:verbose yes; "
            r"mirror --continue --delete --no-perms --verbose=3 "
            r"-X '\.(mirror|notar)' -x '\.in\..*\.' -X 'lost+found' "
            f"ftp://{src} {dst}"
        )

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
