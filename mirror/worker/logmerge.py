import argparse
import os
import signal
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path


_STOP = False


def _request_stop(signum, frame):
    global _STOP
    _STOP = True


@dataclass
class TailSource:
    label: str
    path: Path
    fd: int | None = None
    identity: tuple[int, int] | None = None
    buffer: bytes = b""
    rotated_paths_seen: set[Path] = field(default_factory=set)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            self.identity = None


def _open_regular(path: Path, flags: int, mode: int = 0o644) -> tuple[int, os.stat_result] | None:
    open_flags = flags | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, open_flags, mode)
    except FileNotFoundError:
        return None
    except OSError:
        return None

    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        return None
    return fd, st


def _ensure_source_open(source: TailSource) -> None:
    try:
        current = source.path.stat()
    except FileNotFoundError:
        source.close()
        return

    current_identity = (current.st_dev, current.st_ino)
    if source.identity == current_identity:
        return

    source.close()
    opened = _open_regular(source.path, os.O_RDONLY)
    if opened is None:
        return
    source.fd, st = opened
    source.identity = (st.st_dev, st.st_ino)


def _write_line(dest_fd: int, label: str, line: bytes) -> None:
    # Keep output source-identifiable even when merged in observation order.
    os.write(dest_fd, b"[" + label.encode("utf-8", "replace") + b"] " + line + b"\n")


def _emit_complete_lines(dest_fd: int, source: TailSource, final: bool = False) -> None:
    while True:
        positions = [pos for pos in (source.buffer.find(b"\n"), source.buffer.find(b"\r")) if pos >= 0]
        if not positions:
            break
        pos = min(positions)
        line = source.buffer[:pos]
        next_pos = pos + 1
        if source.buffer[pos:pos + 2] == b"\r\n":
            next_pos += 1
        source.buffer = source.buffer[next_pos:]
        _write_line(dest_fd, source.label, line)

    if final and source.buffer:
        _write_line(dest_fd, source.label, source.buffer)
        source.buffer = b""


def _drain_source(dest_fd: int, source: TailSource, final: bool = False, chunk_size: int = 65536) -> None:
    _ensure_source_open(source)
    if source.fd is None:
        if final:
            _drain_rotated_once(dest_fd, source, chunk_size)
        return

    while True:
        data = os.read(source.fd, chunk_size)
        if not data:
            break
        source.buffer += data
        _emit_complete_lines(dest_fd, source)
    _emit_complete_lines(dest_fd, source, final=final)


def _drain_rotated_once(dest_fd: int, source: TailSource, chunk_size: int) -> None:
    rotated = Path(str(source.path) + ".0")
    if rotated in source.rotated_paths_seen:
        return
    opened = _open_regular(rotated, os.O_RDONLY)
    if opened is None:
        return
    source.rotated_paths_seen.add(rotated)
    fd, _ = opened
    try:
        while True:
            data = os.read(fd, chunk_size)
            if not data:
                break
            source.buffer += data
            _emit_complete_lines(dest_fd, source)
        _emit_complete_lines(dest_fd, source, final=True)
    finally:
        os.close(fd)


def _parse_source(value: str) -> TailSource:
    if "=" not in value:
        raise argparse.ArgumentTypeError("source must be LABEL=PATH")
    label, path = value.split("=", 1)
    if not label or not path:
        raise argparse.ArgumentTypeError("source must be LABEL=PATH")
    return TailSource(label=label, path=Path(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge watched log files into a destination log")
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--source", action="append", default=[], type=_parse_source)
    parser.add_argument("--poll", type=float, default=0.2)
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    opened_dest = _open_regular(args.dest, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    if opened_dest is None:
        raise SystemExit(f"cannot open destination log: {args.dest}")
    dest_fd, _ = opened_dest

    try:
        while not _STOP:
            for source in args.source:
                _drain_source(dest_fd, source)
            time.sleep(args.poll)

        for source in args.source:
            _drain_source(dest_fd, source, final=True)
    finally:
        for source in args.source:
            source.close()
        os.close(dest_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
