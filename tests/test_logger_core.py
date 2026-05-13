import logging
from types import SimpleNamespace

import mirror
import mirror.logger
from mirror.logger.handler import PromptHandler


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
            }
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
