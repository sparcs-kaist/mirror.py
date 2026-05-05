"""Regression tests for has_root_or_sudo."""
from unittest.mock import patch, MagicMock

from mirror.toolbox import has_root_or_sudo


def test_returns_true_when_root():
    with patch("mirror.toolbox.os.getuid", return_value=0):
        assert has_root_or_sudo() is True


def test_uses_subprocess_list_form_not_shell():
    fake = MagicMock(returncode=0)
    with patch("mirror.toolbox.os.getuid", return_value=1000), \
         patch("mirror.toolbox.subprocess.run", return_value=fake) as run:
        assert has_root_or_sudo() is True
        args, kwargs = run.call_args
        assert args[0] == ["sudo", "-n", "true"]
        assert kwargs.get("check") is False
        assert kwargs.get("capture_output") is True


def test_returns_false_on_nonzero_exit():
    fake = MagicMock(returncode=1)
    with patch("mirror.toolbox.os.getuid", return_value=1000), \
         patch("mirror.toolbox.subprocess.run", return_value=fake):
        assert has_root_or_sudo() is False
