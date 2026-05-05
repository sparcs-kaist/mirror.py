"""Regression tests for checkPermission (Commit 1, finding C3)."""
from unittest.mock import patch, MagicMock

from mirror.toolbox import checkPermission


def test_returns_true_when_root():
    with patch("mirror.toolbox.os.getuid", return_value=0):
        assert checkPermission() is True


def test_uses_subprocess_list_form_not_shell():
    fake = MagicMock(returncode=0)
    with patch("mirror.toolbox.os.getuid", return_value=1000), \
         patch("mirror.toolbox.subprocess.run", return_value=fake) as run:
        assert checkPermission() is True
        args, kwargs = run.call_args
        assert args[0] == ["sudo", "-n", "true"]
        assert kwargs.get("check") is False
        assert kwargs.get("capture_output") is True


def test_returns_false_on_nonzero_exit():
    fake = MagicMock(returncode=1)
    with patch("mirror.toolbox.os.getuid", return_value=1000), \
         patch("mirror.toolbox.subprocess.run", return_value=fake):
        assert checkPermission() is False
