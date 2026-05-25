import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mirror
import mirror.logger
from mirror.logger.core import SafeAppendFileHandler


def make_conf(tmp_path):
    return SimpleNamespace(
        logger={
            "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
            "packagelevel": "INFO",
            "packagefileformat": {
                "base": str(tmp_path),
                "folder": "{year}",
                "filename": "{packageid}.{microsecond}.log",
                "gzip": False,
            },
        },
        uid=1000,
        gid=1000,
    )


def get_clean_logger(name: str) -> logging.Logger:
    pkg_logger = logging.getLogger(f"mirror.package.{name}")
    for handler in pkg_logger.handlers[:]:
        handler.close()
        pkg_logger.removeHandler(handler)
    return pkg_logger


def restore_mirror_log(monkeypatch):
    """Ensure mirror.log is a real Logger so caplog can capture warnings.

    Other tests in the suite replace mirror.log with MagicMock and the
    restoration is sometimes incomplete; force a real Logger for tests
    that rely on caplog.
    """
    monkeypatch.setattr(mirror, "log", logging.getLogger("mirror"), raising=False)


def test_reattach_adds_safe_filehandler_for_valid_path(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "conf", make_conf(tmp_path), raising=False)

    log_file = tmp_path / "test.log"
    log_file.write_bytes(b"existing content\n")

    pkg_logger = get_clean_logger("reattach-valid")
    result = mirror.logger.reattach_logger(pkg_logger, log_file, "reattach-valid")

    assert result is True
    file_handlers = [h for h in pkg_logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    assert isinstance(file_handlers[0], SafeAppendFileHandler)
    assert file_handlers[0].baseFilename == str(log_file)

    for handler in pkg_logger.handlers[:]:
        handler.close()
        pkg_logger.removeHandler(handler)


def test_reattach_skips_when_handlers_present(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "conf", make_conf(tmp_path), raising=False)

    log_file = tmp_path / "existing.log"
    log_file.write_bytes(b"data\n")

    pkg_logger = get_clean_logger("reattach-has-handlers")
    dummy = logging.FileHandler(str(log_file))
    pkg_logger.addHandler(dummy)

    result = mirror.logger.reattach_logger(pkg_logger, log_file, "reattach-has-handlers")

    assert result is False
    assert len(pkg_logger.handlers) == 1

    dummy.close()
    pkg_logger.removeHandler(dummy)


def test_reattach_skips_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "conf", make_conf(tmp_path), raising=False)

    missing = tmp_path / "nonexistent.log"
    pkg_logger = get_clean_logger("reattach-missing")

    result = mirror.logger.reattach_logger(pkg_logger, missing, "reattach-missing")

    assert result is False
    assert not pkg_logger.handlers


def test_reattach_rejects_path_outside_base(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(mirror, "conf", make_conf(tmp_path), raising=False)
    restore_mirror_log(monkeypatch)

    outside_path = Path("/etc/passwd")
    pkg_logger = get_clean_logger("reattach-outside")

    with caplog.at_level(logging.WARNING, logger="mirror"):
        result = mirror.logger.reattach_logger(pkg_logger, outside_path, "reattach-outside")

    assert result is False
    assert not pkg_logger.handlers
    assert any("refusing path outside" in rec.message for rec in caplog.records)


def test_reattach_rejects_symlink_path(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(mirror, "conf", make_conf(tmp_path), raising=False)
    restore_mirror_log(monkeypatch)

    target = tmp_path / "target.log"
    target.write_bytes(b"real content\n")
    link = tmp_path / "link.log"
    link.symlink_to(target)

    pkg_logger = get_clean_logger("reattach-symlink")

    with caplog.at_level(logging.WARNING, logger="mirror"):
        result = mirror.logger.reattach_logger(pkg_logger, link, "reattach-symlink")

    assert result is False
    assert not pkg_logger.handlers


def test_reattach_rejects_hardlinked_file(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(mirror, "conf", make_conf(tmp_path), raising=False)
    restore_mirror_log(monkeypatch)

    a_log = tmp_path / "a.log"
    a_log.write_bytes(b"data\n")
    b_log = tmp_path / "b.log"
    os.link(a_log, b_log)

    pkg_logger = get_clean_logger("reattach-hardlink")

    with caplog.at_level(logging.WARNING, logger="mirror"):
        result = mirror.logger.reattach_logger(pkg_logger, a_log, "reattach-hardlink")

    assert result is False
    assert not pkg_logger.handlers
    assert any("hardlinked" in rec.message for rec in caplog.records)


def test_reattach_uses_o_nofollow(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "conf", make_conf(tmp_path), raising=False)

    log_file = tmp_path / "nofollow.log"
    log_file.write_bytes(b"content\n")

    pkg_logger = get_clean_logger("reattach-nofollow")

    real_os_open = os.open
    captured_calls = []

    def spy_os_open(path, flags, *args, **kwargs):
        captured_calls.append((path, flags))
        return real_os_open(path, flags, *args, **kwargs)

    with patch("mirror.logger.core.os.open", side_effect=spy_os_open):
        result = mirror.logger.reattach_logger(pkg_logger, log_file, "reattach-nofollow")

    assert result is True
    assert captured_calls, "os.open was not called"
    for _path, flags in captured_calls:
        assert flags & os.O_NOFOLLOW, f"O_NOFOLLOW not set in flags: {flags}"

    for handler in pkg_logger.handlers[:]:
        handler.close()
        pkg_logger.removeHandler(handler)
