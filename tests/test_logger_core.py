import logging
from types import SimpleNamespace
from unittest.mock import patch

import mirror
import mirror.logger
from mirror.logger.handler import PromptHandler, compress_file, apply_configured_owner


def test_create_logger_replaces_existing_package_handlers(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "debug", False, raising=False)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(
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
        ),
        raising=False,
    )

    first = mirror.logger.create_logger("pkg", 1000.0)
    second = mirror.logger.create_logger("pkg", 1001.0)

    assert first is second
    assert sum(isinstance(handler, PromptHandler) for handler in second.handlers) == 1
    assert sum(isinstance(handler, logging.FileHandler) for handler in second.handlers) == 1
    assert len(second.handlers) == 2

    mirror.logger.close_logger(second, compress=False)


def test_apply_configured_owner_chowns_when_root(tmp_path, monkeypatch):
    path = tmp_path / "owned.log"
    path.write_text("x")
    monkeypatch.setattr(mirror, "conf", SimpleNamespace(uid=123, gid=456), raising=False)

    with patch("mirror.logger.handler.os.geteuid", return_value=0), \
         patch("mirror.logger.handler.os.chown") as chown_mock:
        apply_configured_owner(path)

    chown_mock.assert_called_once_with(path, 123, 456, follow_symlinks=False)


def test_apply_configured_owner_skips_when_not_root(tmp_path, monkeypatch):
    path = tmp_path / "owned.log"
    path.write_text("x")
    monkeypatch.setattr(mirror, "conf", SimpleNamespace(uid=123, gid=456), raising=False)

    with patch("mirror.logger.handler.os.geteuid", return_value=999), \
         patch("mirror.logger.handler.os.getegid", return_value=999), \
         patch("mirror.logger.handler.os.chown") as chown_mock:
        apply_configured_owner(path)

    chown_mock.assert_not_called()


def test_compress_file_applies_configured_owner_to_gzip(tmp_path, monkeypatch):
    path = tmp_path / "session.log"
    path.write_text("log")
    monkeypatch.setattr(mirror, "conf", SimpleNamespace(uid=123, gid=456), raising=False)

    with patch("mirror.logger.handler.os.geteuid", return_value=0), \
         patch("mirror.logger.handler.os.chown") as chown_mock:
        gzip_path = compress_file(path)

    assert gzip_path == tmp_path / "session.log.gz"
    assert gzip_path.exists()
    chown_mock.assert_called_with(gzip_path, 123, 456, follow_symlinks=False)


def test_create_logger_applies_configured_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "debug", False, raising=False)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(
            logger={
                "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
                "packagelevel": "INFO",
                "packagefileformat": {
                    "base": str(tmp_path),
                    "folder": "{year}",
                    "filename": "{packageid}.log",
                    "gzip": False,
                },
            },
            uid=123,
            gid=456,
        ),
        raising=False,
    )

    with patch("mirror.logger.handler.os.geteuid", return_value=0), \
         patch("mirror.logger.handler.os.chown") as chown_mock:
        pkg_logger = mirror.logger.create_logger("pkg-owner", 1000.0)

    try:
        paths = [call.args[0] for call in chown_mock.call_args_list]
        assert tmp_path.resolve() in paths
        assert tmp_path.resolve() / "1970" in paths
        assert tmp_path.resolve() / "1970" / "pkg-owner.log" in paths
    finally:
        mirror.logger.close_logger(pkg_logger, compress=False)
