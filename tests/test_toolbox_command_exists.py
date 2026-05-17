"""Regression tests for command_exists (Commit 1, finding C3)."""

from mirror.toolbox import command_exists


def test_existing_command_returns_true():
    assert command_exists("ls") is True


def test_missing_command_returns_false():
    assert command_exists("definitely-not-a-real-command-xyz123") is False


def test_injection_attempt_returns_false_and_no_side_effect(tmp_path):
    sentinel = tmp_path / "PWNED"
    payload = f"nope; touch {sentinel}"
    assert command_exists(payload) is False
    assert not sentinel.exists()


def test_argument_with_metacharacters_does_not_run_shell(tmp_path):
    sentinel = tmp_path / "PWNED2"
    assert command_exists(f"`touch {sentinel}`") is False
    assert not sentinel.exists()
