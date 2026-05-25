import logging
import time
from types import SimpleNamespace

import mirror
import mirror.logger


def test_exists_false_when_no_handlers():
    pkg_logger = logging.getLogger("mirror.package.test-exists-empty")
    for handler in pkg_logger.handlers[:]:
        pkg_logger.removeHandler(handler)

    assert mirror.logger.exists("test-exists-empty") is False


def test_exists_true_after_create_logger(tmp_path, monkeypatch):
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

    pkg_logger = mirror.logger.create_logger("test-exists-after", time.time())
    try:
        assert mirror.logger.exists("test-exists-after") is True
    finally:
        mirror.logger.close_logger(pkg_logger, compress=False)


def test_exists_false_after_close_logger(tmp_path, monkeypatch):
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

    pkg_logger = mirror.logger.create_logger("test-exists-closed", time.time())
    mirror.logger.close_logger(pkg_logger, compress=False)
    assert mirror.logger.exists("test-exists-closed") is False
