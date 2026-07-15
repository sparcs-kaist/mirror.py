"""Tests for the apt-mirror sync method."""
import logging
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.structure
import mirror.sync.apt_mirror as apt_mirror


def _make_package(options=None):
    pkg = MagicMock(spec=mirror.structure.Package)
    pkg.pkgid = "test-apt-mirror"
    pkg.name = "Test apt-mirror"
    pkg.synctype = "apt-mirror"
    pkg.settings = MagicMock()
    pkg.settings.src = ""
    pkg.settings.dst = ""
    pkg.settings.options = options if options is not None else {}
    return pkg


def test_build_command_default_configfile():
    """With no configfile option, the default mirror.list path is used."""
    assert apt_mirror._build_command({}) == ["apt-mirror", "/etc/apt/mirror.list"]


def test_build_command_custom_configfile():
    """A custom configfile option is passed through as the sole argument."""
    command = apt_mirror._build_command({"configfile": "/etc/apt/custom.list"})
    assert command == ["apt-mirror", "/etc/apt/custom.list"]


@pytest.mark.parametrize(
    "value",
    [
        "-oPwn",           # option injection via leading dash
        "",                # empty
        "with\x00nul",     # NUL byte
        "with\nnewline",   # control character
        123,               # not a string
    ],
)
def test_validate_configfile_rejects_bad_values(value):
    """Malformed configfile values are rejected before reaching the worker."""
    with pytest.raises(ValueError):
        apt_mirror._validate_configfile(value)


@pytest.mark.parametrize("value", ["-oPwn", "", "with\x00nul", "with\nnewline"])
@patch("mirror.sync.on_sync_done")
@patch("mirror.socket.worker.execute_command")
def test_execute_rejects_bad_configfile_before_worker(mock_execute_command, mock_on_sync_done, value):
    """A malformed configfile must fail the sync without reaching the worker."""
    mirror.conf = MagicMock()
    mirror.conf.uid = 0
    mirror.conf.gid = 0

    logger = logging.getLogger("test-apt-mirror-badcfg")
    logger.handlers = []
    pkg = _make_package({"configfile": value})

    apt_mirror.execute(pkg, logger)

    mock_execute_command.assert_not_called()
    mock_on_sync_done.assert_called_once_with("test-apt-mirror", success=False, returncode=None)


@patch("mirror.socket.worker.execute_command")
def test_execute_delegates_to_worker(mock_execute_command):
    """execute() delegates the apt-mirror command to the worker."""
    mirror.conf = MagicMock()
    mirror.conf.uid = 1234
    mirror.conf.gid = 5678
    mock_execute_command.return_value = {"status": "started", "job_pid": 42}

    logger = logging.getLogger("test-apt-mirror")
    logger.handlers = []
    pkg = _make_package({"configfile": "/etc/apt/mirror.list"})

    apt_mirror.execute(pkg, logger)

    mock_execute_command.assert_called_once()
    kwargs = mock_execute_command.call_args.kwargs
    assert kwargs["sync_method"] == "apt-mirror"
    assert kwargs["commandline"] == ["apt-mirror", "/etc/apt/mirror.list"]
    assert kwargs["job_id"] == "test-apt-mirror"


@patch("mirror.sync.on_sync_done")
@patch("mirror.socket.worker.execute_command", side_effect=RuntimeError("boom"))
def test_execute_marks_failed_on_error(mock_execute_command, mock_on_sync_done):
    """A worker failure marks the sync as failed via on_sync_done."""
    mirror.conf = MagicMock()
    mirror.conf.uid = 0
    mirror.conf.gid = 0

    logger = logging.getLogger("test-apt-mirror-fail")
    logger.handlers = []
    pkg = _make_package()

    apt_mirror.execute(pkg, logger)

    mock_on_sync_done.assert_called_once_with("test-apt-mirror", success=False, returncode=None)
